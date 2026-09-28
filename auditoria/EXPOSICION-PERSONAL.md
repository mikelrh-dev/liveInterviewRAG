# Exposición de datos personales — informe

**Fecha:** 2026-09-28
**Repositorio:** `github.com/mikelrh-dev/liveInterviewRAG` — **PÚBLICO** (verificado vía API)
**Estado:** las 46 páginas de `wiki/` siguen rastreadas en `HEAD`.

---

## 1. Qué está expuesto ahora mismo

### Identificadores directos

| Dato | Dónde |
|---|---|
| Nombre completo: **Mikel Romero Homobono** | `wiki/profile/mikel.md` |
| Ubicación: **Barakaldo, Vizcaya, España** | `wiki/profile/mikel.md` |
| Email de autor en el historial de commits | `ccc24ff` (autor: `mikelromerohomobono@gmail.com`) |

### Historial profesional con fechas

| Fichero | Contenido |
|---|---|
| `wiki/experience/gerente-mercadona-2019-2025.md` | Gerente B en Mercadona, 2019 – Nov 2025, tienda ~100 personas, equipo directo ~50 |
| `wiki/experience/encargado-bm-2016-2019.md` | Encargado en BM, 2016-2019 |
| `wiki/experience/frutero-bm-2015-2016.md` | Frutero en BM, 2015-2016 |

### Perfil profesional

Nivel de inglés (B2), euskera (intermedio), habilidades, proyectos, decisiones
de carrera, opiniones, historias personales. 11 categorías en total:

```
profile 1 · experience 3 · projects 3 · skills 5 · stories 9
opinions 4 · decisions 4 · faq 8 · templates 8 · CONVENCIONES 1
```

### Lo que **no** está expuesto

- **Ningún `.env` en el historial** — verificado, no hay secretos.
- **Sin emails ni teléfonos** en el contenido de la wiki.
- Sin claves de API, sin tokens, sin ficheros de base de datos.

---

## 2. Por qué sigue expuesto

Un solo commit añadió el contenido:

```
67ad63b  feat(wiki): publish candidate wiki to repo for VPS RAG ingestion
```

Y el intento de dejar de rastrearlo:

```
ccc24ff  chore(repo): stop tracking personal candidate/ and wiki/ data
```

`ccc24ff` ejecutó `git rm -r --cached` — es decir, **dejó de rastrear los ficheros
pero no los borró de los commits anteriores**. La regla `/wiki/` en `.gitignore`
solo afecta a ficheros **nuevos**: nunca deshace lo ya registrado.

Y como las 46 páginas **siguen en `HEAD`**, no están solo en el historial: se ven
navegando el repo ahora mismo.

---

## 3. Qué implica cada opción

### Opción A — Purgar el historial

Reescribir el historial para sacar `wiki/` de todos los commits, y añadir un guard
que impida que vuelva a colarse.

| | |
|---|---|
| **Elimina** | Las páginas del historial y de `HEAD`. El repo deja de exponerlas |
| **Requiere** | Force-push. Coordinate force con quien colabore en el repo |
| **Límite real** | **Quien ya haya clonado el repo conserva los datos en su copia.** Borrar del remoto no borra de clones ya existentes |
| **Reversible** | No. Reescribir historia es irreversible sin un backup |
| **Costo** | Todos los commits cambian de SHA. Abrir issues y PRs se desconectan |

### Opción B — Repositorio privado + guard

| | |
|---|---|
| **Elimina** | La exposición pública inmediata |
| **Requiere** | Cambiar la visibilidad a privada. Añadir el guard |
| **Límite real** | Las páginas **siguen en el historial**. Los enlaces de GitHub dejan de funcionar, pero el dato no desaparece |
| **Reversible** | Sí, totalmente |
| **Costo** | Un repo privado de portfolio es un handicap: nadie lo ve sin invitación |

### Opción C — Solo el guard

Añadir el guard que impide que `wiki/` vuelva a colarse, y no tocar nada más.

| | |
|---|---|
| **Elimina** | Nada de lo ya expuesto |
| **Útil** | Evita que la fuga crezca con los commits de esta sesión |
| **Costo** | Ninguno |

---

## 4. Mi recomendación

**Opción B ahora, Opción A después si el repo va a seguir siendo público.**

Razón: la Opción A reescribe historia y es irreversible, y no la ejecutaría sin
confirmar primero que nadie más trabaja en este repo. La Opción B cierra la
exposición pública hoy, es reversible, y no rompe los SHA de nadie.

Cuando decidas si el repo sigue siendo público —y si quieres que un reclutador
pueda verlo sin invitación—, entonces la Opción A tiene sentido y hay que hacerla
**antes** de mergear más commits, porque después habrá más que reescribir.

**En cualquier caso, el guard es urgente e independiente**: esta sesión ha
commiteado muchas veces, y nada impide que alguien vuelva a hacer
`git add -f wiki/...`.

---

## 5. Una nota

`wiki/` es un **repositorio git anidado** (tiene su propio `.git/`), que es lo que
permite al padre rastrear ficheros dentro de él. Mientras esa estructura exista,
cualquier `git add -A` desde la raíz puede volver a colar contenido. El guard
debe cubrir eso, no solo el `.gitignore`.
