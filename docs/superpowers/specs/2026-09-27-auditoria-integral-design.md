# Diseño: Auditoría Integral de InterviewTTS

**Fecha:** 2026-09-27
**Alcance:** Auditoría técnica, funcional, visual y de contenido sobre el repositorio completo
**Entregables:** informe de hallazgos + plan de acción priorizado por severidad técnica
**Enfoque:** por capas de riesgo (ejecución secuencial, de mayor a menor severidad)

---

## 1. Objetivo

Encontrar fallos potenciales del sistema y proponer mejoras funcionales y visuales, con el
resultado clasificado por severidad técnica para que la remediación pueda atacarse en orden de
riesgo real, no de ruido percibido.

## 2. Criterios de priorización

Cada hallazgo se clasifica en uno de cuatro niveles:

| Nivel | Definición |
|---|---|
| **CRÍTICO** | Riesgo de seguridad, pérdida de datos o indisponibilidad en producción |
| **ALTO** | Fallo de fiabilidad bajo carga o degradación silenciosa del pipeline de voz |
| **MEDIO** | Incidencia en límites de módulo, mantenibilidad o experiencia de usuario en casos borde |
| **BAJO** | Mejora de claridad, consistencia visual o pulido sin impacto operativo |

El criterio de orden es **severidad técnica**, no esfuerzo ni ruido percibido. Un hallazgo
CRÍTICO de remediación costosa se entrega igual y por delante de un MEDIO trivial.

## 3. Capas de auditoría

### Capa 1 — Seguridad e integridad de datos (CRÍTICO)

Superficie de ataque del servicio y exposición de datos.

- Tratamiento de secretos: presencia de `.env` en la raíz, contenido de `.env.example`, qué
  expone el endpoint `/api/config` y si algún secreto llega al cliente.
- Validación de entrada en la ruta de audio: tamaño máximo, derivación de extensión a partir
  de `content_type`, escritura en disco, nombres de archivo generados.
- Riesgo de path traversal en el montaje `StaticFiles` sobre `/audio`.
- Inyección SQL en la capa de persistencia SQLite: construcción de consultas con
  concatenación frente a parámetros.
- Rate limiting declarado frente a rate limiting aplicado.
- CORS, cabeceras de seguridad, terminación TLS.
- Vulnerabilidades de dependencias declaradas.
- Pérdida de datos: poda por TTL, rotación de caché, consistencia entre memoria y SQLite.

### Capa 2 — Fiabilidad y concurrencia (ALTO)

Comportamiento bajo el régimen real de un VPS compartido de 4 núcleos.

- Bloqueo del event loop: qué operaciones CPU-bound o bloqueantes escapan a
  `asyncio.to_thread`.
- Corrección del stream SSE y manejo de cancelación del cliente a mitad de la generación.
- El supuesto de una conversación simultánea: comportamiento real con varias
  conversaciones simultáneas.
- Degradación elegante: failover de LLM, fallback de TTS, fallo de Whisper, ambos proveedores
  caídos.
- Fugas de recursos: archivos temporales de audio, ciclo de vida de modelos, crecimiento de
  estructuras en memoria.
- Manejo de timeouts en cada salto del pipeline.
- Consistencia entre el estado en memoria y el estado persistido.

### Capa 3 — Rendimiento y calidad de RAG (ALTO / MEDIO)

Presupuesto de latencia y corrección de la recuperación.

- Descomposición real de la latencia por salto frente a los 8-12 s documentados.
- Coste de retrieval: embedding, similitud coseno, dimensionality y construcción de índices.
- Efectividad real de la caché FAQ y de la caché semántica: tasa de acierto, umbral de
  similitud, distribución de puntuaciones.
- Consumo de RAM y de CPU con Whisper, embedder y cliente LLM simultáneos.
- Eficiencia del streaming SSE: granularidad, backpressure, eventos por token.
- Análisis de la calidad de los chunks: longitud, solapamiento, fragmentación, coherencia
  semántica.

### Capa 4 — Arquitectura y calidad de código (MEDIO)

- `backend/main.py` como punto de concentración: tamaño, responsabilidades mezcladas,
  acoplamiento con los servicios.
- Límites de módulo: qué vive en `services/`, qué debería vivir en un router o en un
  orquestador.
- Duplicación de lógica.
- Código muerto o inalcanzable.
- Configuración: dispersión, valores por defecto, validación.
- Claridad de nombres y de intención del código.

### Capa 5 — Funcional y experiencia de usuario (MEDIO)

- Recorrido completo del reclutador y puntos de abandono.
- Estados de error: qué ve el usuario cuando falla STT, LLM o TTS.
- Casos borde de la lógica de conversación: despedida, VAD, primer turno sustantivo,
  rehidratación desde base de datos, TTL.
- Accesibilidad: teclado, lectores de pantalla, foco, contraste.
- Responsive y comportamiento en móvil.
- La superposición de disclaimer experimental añadida en el último commit.

### Capa 6 — Visual (BAJO)

- Consistencia del sistema de diseño: tokens, espaciado, color, tipografía.
- Movimiento y coreografía: coherencia, duración, respeto por `prefers-reduced-motion`.
- Estado del avatar y sincronización con el audio.
- Legibilidad en condiciones de uso real (pantallas pequeñas, brillo alto).

### Capa 7 — Contenido de la wiki (MEDIO)

Reejecuta y actualiza `AUDITORIA-2026-08-28.md`.

- Estado de resolución de los 22 marcadores TODO identificados en agosto.
- Estado de las 14 páginas con `confidence: medium`: promovidas, corregidas o sin tocar.
- Contenido añadido desde agosto: ¿trae marcadores nuevos o confianza incorrecta?
- Verificación de que el chunker del RAG no sirve marcadores TODO como si fueran datos.

## 4. Entregables

1. **`AUDITORIA-2026-09-27.md`** — informe completo. Por cada hallazgo: capa, título,
   severidad, evidencia con `archivo:línea`, impacto observado o inferido, y recomendación
   concreta.
2. **Plan de acción priorizado** — sección final del mismo informe. Tabla ordenada por
   severidad con: acción, archivos afectados, dependencia entre acciones y estimación de
   esfuerzo.

Ambos entregables en un único documento, para que el informe y su plan de remediación no
se separen.

## 5. Ejecución

- Las capas se recorren **en orden**, deteniéndose si una capa revela un hallazgo CRÍTICO que
  invalide el análisis posterior.
- Cada capa se ejecuta con reader de contexto fresco para evitar contaminación de hallazgos
  previos.
- La verificación de los hallazgos de las capas 1, 2 y 3 incluye ejecución real de la
  suite de tests y, cuando el hallazgo lo requiere, reproducción del fallo.
- El informe se presenta al usuario antes de cualquier cambio en el código. La auditoría
  **no modifica** el producto: solo produce el informe y el plan.

## 6. Fuera de alcance

- Corrección de los hallazgos. La auditoría identifica y prioriza; la remediación es un
  cambio posterior con su propio ciclo.
- Auditoría de los repositorios externos o del wiki privado de respaldo.
- Revisión de la configuración de la cuenta de Oracle Cloud más allá de lo que el repositorio
  declara.
- Cualquier prueba que requiera credenciales de proveedor no presentes en el entorno.

## 7. Riesgos de la propia auditoría

- **Falsos positivos por análisis estático**: un hallazgo marcado CRÍTICO que dependa de un
  supuesto no verificado se reetiqueta antes de entregarse, con el supuesto explícito.
- **Auditoría estática no sustituye a pruebas de carga**: las cifras de latencia y de
  concurrencia se marcan como medidas o como inferidas, nunca se presentan como medición si
  no lo son.
- **Superficie demasiado grande para una sola pasada**: si una capa produce hallazgos que
  requieren investigación adicional, se anota como tal en lugar de deepening en el sitio y
  perder el resto de las capas.
