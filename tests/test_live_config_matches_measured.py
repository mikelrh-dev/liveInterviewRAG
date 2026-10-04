"""Las cifras de retrieval están atadas a un embedder; el que corre debe ser ese.

LA AFIRMACIÓN
-------------
``README.md:129`` publica una cifra que sólo es cierta en un espacio vectorial
concreto: el cambio ``all-MiniLM-L6-v2`` -> ``paraphrase-multilingual-MiniLM-L12-v2``
"**alone**", con el modelo como única palanca tocada, mueve recall@3 de 0.653 a
0.796 sobre las 49 preguntas etiquetadas. ``.env.example:63-69`` dice lo mismo y
ancla el número, y ``backend/config.py:138-145`` lleva el par antes/después en el
mismo bloque que publica el estado vigente.

El invariante que ata las tres cosas es uno solo:

    Las cifras de retrieval publicadas están atadas a un embedder concreto.
    El embedder que se ejecuta en runtime debe ser ese.

``tests/real_wiki.py`` ya declara cuál es ese: ``FLOOR_EMBEDDER`` es
``EMBEDDING_MODEL`` y ``FLOOR_SET_BY`` dice la fecha y el modelo con el que se
midió. Este módulo CONSUME esos dos símbolos en lugar de re-declarar la cadena,
y esa es la razón de ser del diseño: si mañana se re-mediden los floors bajo otro
embedder, la constante se mueve en un sitio y este guard se mueve con ella. Una
copia del nombre sería un cuarto lugar donde el modelo está escrito, y un cuarto
lugar es exactamente el problema que este archivo existe para detectar.

EL DEFECTO QUE ATRAPA
---------------------
Tres fuentes decían qué embedder corre, y divergieron:

    backend/config.py:146       paraphrase-multilingual-MiniLM-L12-v2
    .env.example:70             paraphrase-multilingual-MiniLM-L12-v2
    .env (vivo, gitignored)     all-MiniLM-L6-v2              <-- el que gana

``backend/main.py:39`` llama a ``load_dotenv()`` ANTES del ``from backend.config
import config`` de ``backend/main.py:46``, y ``backend/config.py:278`` construye
``Config()`` en el momento de importarse. Comprobado en esta máquina, en ese
orden exacto:

    >>> from backend.config import config                    # sin load_dotenv
    >>> config.EMBEDDING_MODEL
    'paraphrase-multilingual-MiniLM-L12-v2'
    >>> from dotenv import load_dotenv; load_dotenv()
    >>> from backend.config import config
    >>> config.EMBEDDING_MODEL
    'all-MiniLM-L6-v2'

Es decir que la aplicación corría el embedder INGLÉS: la configuración de
recall@3 0.653 que esa misma fila del README dice haber sustituido por la de
0.796. Y ``/api/health`` reportaba ``ok`` igualmente, porque la salud del
servicio no depende de en qué espacio vectorial vive.

Ningún guard existente lo veía:

* ``tests/test_config_surface.py`` ata ``.env.example`` a ``backend/config.py``
  en las DOS direcciones, pero nunca abre el ``.env`` real, que es el único
  fichero que gana.
* ``tests/test_rag_cache_identity.py`` ata ``backend/config.py`` a
  ``backend/services/rag.py``.
* ``tests/real_wiki.py`` declara su propio ``EMBEDDING_MODEL`` y construye sus
  pipelines con él, de modo que la suite verde mide una configuración que la
  aplicación no ejecuta. Un guard que se compara consigo mismo no mide nada.

POR QUÉ EL VALOR EFECTIVO SE LEE DEL FICHERO Y NO DEL OBJETO ``config``
-----------------------------------------------------------------------
Porque dentro del proceso de pytest el objeto ``config`` miente, y miente
justo en la dirección que vuelve verde este guard.

``tests/conftest.py:29`` hace ``from backend.config import config`` a nivel de
módulo, y eso ocurre al recoger la suite, antes de que nadie llame a
``load_dotenv()``. Para cuando ``backend.main`` se importa en el fixture autouse
``isolated_write_targets`` (``tests/conftest.py:111``), ``Config()`` ya está
construido y su ``EMBEDDING_MODEL`` ya está congelado con el default, no con lo
que dice el ``.env``. El primer caso del ``>>>`` de arriba ES el estado en el que
vive la suite.

Un test que leyera ``config.EMBEDDING_MODEL`` afirmaría que la aplicación carga
el multilingüe mientras la aplicación carga el inglés: pasaría siempre y no
atraparía nada. Por eso el valor efectivo se resuelve LEYENDO ``.env``, que es lo
que hace ``python-dotenv``, y sólo se recurre al default de ``backend/config.py``
cuando el fichero no existe o la línea está comentada.

QUÉ SE LEE Y CÓMO
-----------------
1. ``.env`` y ``.env.example``, con ``_ASSIGNMENT`` importado de
   ``tests/test_config_surface.py:123`` -- el mismo parser con el que ese módulo
   sincroniza el template. Importado y no copiado a propósito: dos reglas
   distintas de "qué es una asignación" dentro del mismo repositorio divergen, y
   la segunda no la lee nadie.
2. El default de ``EMBEDDING_MODEL`` en ``backend/config.py``, con :mod:`ast` y
   no con una regex. ``tests/test_config_surface.py:39-42`` explica el motivo, y
   el ejemplo es este mismo repositorio: ``backend/main.py:289`` menciona
   ``os.getenv("CORS_ORIGINS")`` dentro de un comentario que explica lo que hacía
   el código viejo, así que una regex lee ese comentario como un sitio de
   lectura. Aquí el riesgo es el espejo -- un comentario nombrando el modelo -- y
   el AST no ve comentarios en absoluto.
3. El embedder que los floors miden: ``tests.real_wiki.FLOOR_EMBEDDER``, que
   este archivo importa.

QUÉ NO HACE ESTE MÓDULO
-----------------------
No afirma que TODO valor de ``.env`` sea igual al default de ``backend/config.py``.
Sería incorrecto y dejaría el módulo sin utilidad: un operador que baja
``WHISPER_MODEL`` a ``tiny`` porque su máquina es un portátil sin GPU está
tomando una decisión legítima sobre su hardware, y ``OPENROUTER_API_KEY`` no
puede tener un default en absoluto. Un guard así no protege nada: rompe el ajuste
legítimo y, el día en que alguien lo acepta para poder trabajar, se borra a sí
mismo por cansancio.

El alcance es UNA clave, la única cuyo valor es un CONTRATO DE MEDICIÓN: un
número publicado en el README sólo es cierto en el espacio vectorial que la
produjo. Las demás declaran preferencias de una máquina, que es justo lo que un
operador tiene derecho a cambiar sin que el repositorio le discuta.

Tampoco comprueba que ``.env`` exista, ni lo escribe, ni lo versiona, y no
sustituye a ``tests/test_config_surface.py``: ese módulo es dueño de las dos
direcciones entre ``.env.example`` y ``backend/config.py``. Este sólo añade la
tercera fuente, la que gana.

``.env`` ESTÁ EN ``.gitignore`` Y ESTO NO SE OCULTA
--------------------------------------------------
``.gitignore:21`` lista ``.env``, así que este módulo sólo tiene dientes en la
máquina del desarrollador. En un clon limpio no hay ``.env``, se toma la rama del
default y la primera comprobación pasa -- y pasa por una razón real, que es
justamente lo que este módulo afirma: sin ``.env`` la aplicación carga el
default, y el default es el embedder medido. La segunda comprobación se salta,
con motivo escrito, porque no hay dos ficheros que comparar.

Es una limitación honesta y no un fallo: un clon limpio ya viene con el embedder
correcto y no tiene nada que desincronizar. Lo que aquí se comprueba -- que el
fichero vivo no se separa del template en silencio -- es una propiedad de la
máquina y no del repositorio. Lo que sí es del repositorio, y queda fuera de
este alcance, es que nadie escriba un ``.env`` con otro embedder: eso es un
cambio de producto y tiene que pasar por ``tests/real_wiki.py`` y por volver a
medir.
"""

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.real_wiki import FLOOR_EMBEDDER, FLOOR_SET_BY

#: El parser de asignaciones y el lector de nombres con punto se IMPORTAN de
#: ``tests/test_config_surface.py``, que es el módulo que ya los posee y ya
#: justifica sus reglas. Son nombres privados a propósito en su origen, y se
#: aceptan aquí igualmente: la alternativa -- un segundo parser con reglas
#: ligeramente distintas -- divergiría de él en silencio, que es el defecto que
#: ese módulo existe para detectar en las dos direcciones de la sincronización.
#: Si allí se renombran, este archivo falla al importar, que es ruido y no una
#: pase falsa.
from tests.test_config_surface import _ASSIGNMENT, _dotted_name, _first_argument

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PY = REPO_ROOT / "backend" / "config.py"
ENV_FILE = REPO_ROOT / ".env"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

#: La única clave cuyo valor es un CONTRATO DE MEDICIÓN y no una preferencia de
#: máquina. Ver "QUÉ NO HACE ESTE MÓDULO": el alcance es esta clave y ninguna
#: más, y el motivo es que las cifras de retrieval publicadas en
#: ``README.md:129`` y en ``.env.example:63-69`` sólo son ciertas en el espacio
#: vectorial que las produjo. Se nombra como constante y no como literal
#: disperso para que ampliar el scope sea una edición visible.
EMBEDDING_KEY = "EMBEDDING_MODEL"

#: Un nombre de modelo es un único token: sin comillas, sin ``#``, sin espacios
#: interiores. Se comprueba sobre cada valor que este módulo consume, para que un
#: lector que desmonte mal una línea falle aquí y ruidosamente en vez de
#: comparar media línea contra un floor. La alternativa -- resolver la misma
#: línea por un segundo método y exigir que las dos respuestas coincidan -- se
#: descartó por ser dos implementaciones de una idea en un mismo archivo, que es
#: la forma de fallo que ``tests/test_config_surface.py`` ya previene para su
#: propio parser.
_MODEL_NAME_SHAPE = re.compile(r"^\S+$")


@dataclass(frozen=True)
class Declaration:
    """Un valor y dónde está escrito, para que un fallo pueda citar ``fichero:línea``.

    ``form`` distingue ``active`` de ``commented`` porque son hechos distintos
    sobre el fichero: ``python-dotenv`` nunca lee una línea comentada, así que
    una comparación que no los distinga no puede decir qué encontró.
    """

    value: str
    site: str
    form: str


def _value(line: str) -> str:
    """El lado derecho de una línea de dotenv.

    Deliberadamente estrecho, y declarado como tal. El orden importa y lo fija
    ``python-dotenv``: si el valor EMPIEZA por una comilla, ésa es una cadena
    citada y termina en su comilla de cierre, de modo que un ``#`` detrás es
    parte del valor y no abre un comentario; si NO empieza por comilla, el valor
    termina en el primer ``#`` precedido de espacio. Comprobar que el primer y el
    último carácter sean la misma comilla -- que es la lectura intuitiva -- falla
    en el caso más común de todos, ``KEY="value"  # nota``, porque ahí el último
    carácter es la ``a`` de la nota. El resto de la gramática de dotenv
    (interpolación de variables, valores multilínea, comillas sin cerrar) NO se
    maneja; lo que se maneja mal no puede pasar por una comprobación limpia,
    porque la forma del valor se exige más abajo.
    """
    raw = line.partition("=")[2].strip()
    if raw[:1] in ("'", '"'):
        quote = raw[0]
        closing = raw.find(quote, 1)
        return raw[1:closing] if closing > 0 else raw[1:]
    return raw.split(" #", 1)[0].strip()


def _declarations(path: Path) -> dict[str, list[Declaration]]:
    """Toda asignación de ``path``, conservando las formas activa y comentada.

    La división activa/comentada es la de ``_declared()`` en
    ``tests/test_config_surface.py:295-310``: un ``#`` inicial convierte la línea
    en documentación, no en configuración, y las dos formas se reportan por
    nombre para que una clave que degrada de activa a comentada sea visible en
    el fallo y no invisible en una ejecución verde.
    """
    declarations: dict[str, list[Declaration]] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, 1):
        match = _ASSIGNMENT.match(line)
        if not match:
            continue
        declarations.setdefault(match.group(1), []).append(
            Declaration(
                value=_value(line),
                site=f"{path.name}:{number}",
                form="commented" if line.lstrip().startswith("#") else "active",
            )
        )

    # El suelo de no-vacuidad, y es un suelo y no un test porque un parser roto
    # es INVISIBLE en este módulo: una línea que no se lee se confunde con "no
    # declarada", lo que toma la rama del default y hace pasar la primera
    # comprobación por un motivo equivocado.
    #
    # Su alcance es exactamente el fallo que cabe temer de un parser COMPARTIDO:
    # que un cambio en tests/test_config_surface.py lo deje de reconocer líneas de
    # dotenv reales, con lo que este módulo se quedaría comparando el default
    # contra el floor y daría verde sin mirar nada. Para ese caso el fallo tiene
    # que ser un fallo, y no un checkout sano.
    #
    # Lo que NO cubre, y conviene saber: un fallo PARCIAL, en el que se pierde una
    # línea concreta y el resto se lee. Ahí no se puede elevar a error sin
    # rechazar configuración legítima -- ``config.EMBEDDING_MODEL=x`` no es una
    # asignación de dotenv y python-dotenv tampoco la carga como tal, así que
    # reportarla como "no declarada" es la respuesta correcta y no un olvido.
    if not declarations and any("=" in line for line in lines):
        raise AssertionError(
            f"{path} contiene '=' pero _ASSIGNMENT no resolvió ninguna "
            "declaración. O el parser de tests/test_config_surface.py ha dejado "
            "de reconocer líneas de dotenv reales, o este módulo está leyendo un "
            "fichero que no sabe leer. Ambas cosas hacen vacua la comprobación "
            "de abajo."
        )
    return declarations


def _active(path: Path, key: str) -> Declaration | None:
    """La declaración ACTIVA de ``key`` en ``path``, o None si no la hay."""
    found = [
        declaration
        for declaration in _declarations(path).get(key, [])
        if declaration.form == "active"
    ]
    assert len(found) <= 1, (
        f"{path.name} declara {key} de forma activa {len(found)} veces: "
        + ", ".join(declaration.site for declaration in found)
        + ". python-dotenv se queda con la última, así que qué valor corre lo "
        "decide el orden de las líneas en vez de la intención."
    )
    return found[0] if found else None


def _model_name(declaration: Declaration, key: str) -> str:
    """El valor de la declaración, comprobado contra la forma de un nombre de modelo."""
    assert declaration.value, (
        f"{declaration.site} declara {key} con valor vacío. python-dotenv carga "
        "eso como cadena vacía, así que la aplicación le pasaría un nombre de "
        "modelo vacío a sentence-transformers en vez de recurrir al default: eso "
        "es una configuración rota, no una configuración ausente."
    )
    assert _MODEL_NAME_SHAPE.match(declaration.value), (
        f"{declaration.site} resuelve {key} a {declaration.value!r}, que no es un "
        "nombre de modelo desnudo. El lector de este módulo maneja comillas y un "
        "comentario en línea, nada más, así que un valor que no lee limpiamente es "
        "un lector equivocado y no una configuración inusual."
    )
    return declaration.value


def _config_default() -> tuple[str, str]:
    """El default de ``EMBEDDING_MODEL`` en ``backend/config.py``, y su sitio.

    Leído con :mod:`ast` por el motivo que da ``tests/test_config_surface.py:39-42``
    y que está copiado en el docstring de este módulo. Falla ruidosamente en
    cuanto la forma no es la esperada: un default ausente aquí no significa "no
    hay valor que comprobar", significa que la clave se lee de una manera que
    este test no sabe resolver, y eso se reporta por su nombre.
    """
    tree = ast.parse(CONFIG_PY.read_text(encoding="utf-8"), filename=str(CONFIG_PY))
    found: list[tuple[str, ast.Call]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = list(node.targets)
        else:
            continue
        # ``self.EMBEDDING_MODEL`` y ``EMBEDDING_MODEL`` importan igual: se toma
        # el último tramo del nombre con punto, que es el de la clave.
        named = any(
            (_dotted_name(target) or "").rsplit(".", 1)[-1] == EMBEDDING_KEY
            for target in targets
        )
        if named and isinstance(node.value, ast.Call):
            found.append((f"{CONFIG_PY.name}:{node.lineno}", node.value))

    assert len(found) == 1, (
        f"se esperaba exactamente una asignación a {EMBEDDING_KEY} en "
        f"{CONFIG_PY.name} y hay {len(found)}"
        + (f" ({', '.join(site for site, _ in found)})" if found else "")
        + ". Este guard resuelve el valor al que recurriría la aplicación cuando "
        "no hay .env, y una clave asignada en cero sitios -- o en dos -- significa "
        "que ese valor se decide en un sitio que este test no puede ver."
    )

    site, call = found[0]
    assert _dotted_name(call.func) == "os.getenv", (
        f"{site} asigna {EMBEDDING_KEY} desde "
        f"{_dotted_name(call.func) or type(call.func).__name__} y no desde "
        "os.getenv. La clave se lee en un punto que este guard no sabe resolver, "
        "así que su default no es averiguable desde aquí."
    )

    argument = _first_argument(call)
    assert (
        isinstance(argument, ast.Constant)
        and isinstance(argument.value, str)
        and argument.value == EMBEDDING_KEY
    ), (
        f"{site} lee el entorno mediante "
        f"{getattr(argument, 'value', type(argument).__name__)!r} en vez del "
        f"literal {EMBEDDING_KEY!r}. Un nombre de clave que llega por variable no "
        "se puede comprobar contra un floor, y omitirlo sería una pase falsa."
    )
    assert len(call.args) == 2, (
        f"{site} lee {EMBEDDING_KEY} con {len(call.args)} argumento(s) posicional(es). "
        'Este guard espera os.getenv("EMBEDDING_MODEL", <default literal>): sin '
        "default no hay nada a lo que recurrir, y con más de uno el valor en uso no "
        "es el segundo argumento."
    )

    default = call.args[1]
    assert isinstance(default, ast.Constant) and isinstance(default.value, str), (
        f"{site} da a {EMBEDDING_KEY} un default que no es un literal de cadena "
        f"({type(default).__name__}). Una expresión no se puede comparar con un "
        "nombre de modelo sin evaluarla, y evaluarla aquí sería ejecutar código "
        "de producción desde un guard."
    )
    return default.value, site


def _effective() -> tuple[str, str]:
    """El valor de ``EMBEDDING_MODEL`` que la aplicación cargaría, y de dónde sale.

    Reproduce lo que hace ``backend/main.py:39`` + ``backend/main.py:46`` +
    ``backend/config.py:278`` en runtime: ``python-dotenv`` carga ``.env`` en el
    entorno y sólo después se construye ``Config()``. El fichero ``.env`` se lee
    aquí, y no ``config.EMBEDDING_MODEL``, porque en un proceso de pytest ese
    objeto ya está congelado desde el default -- está en el docstring de este
    módulo, con la salida medida.

    La rama del default es una rama real y no una cortesía: sin ``.env`` la
    aplicación carga el default, y el default es entonces el valor en vigor.
    """
    if ENV_FILE.is_file():
        declared = _active(ENV_FILE, EMBEDDING_KEY)
        if declared is not None:
            return _model_name(declared, EMBEDDING_KEY), declared.site
    return _config_default()


def _template() -> Declaration:
    """La declaración activa de la clave en ``.env.example``."""
    declared = _active(ENV_EXAMPLE, EMBEDDING_KEY)
    assert declared is not None, (
        f"{ENV_EXAMPLE.name} no declara {EMBEDDING_KEY} en una línea activa, así que "
        "no enuncia ningún valor de envío para la clave que copia todo operador. "
        "tests/test_config_surface.py es dueño de la otra mitad de esto (que la "
        "clave esté declarada, comentada o no); este módulo necesita el valor en "
        "sí, y una declaración comentada es una clave documentada que el operador "
        "tiene que encender, no la configuración que se envía."
    )
    return declared


def test_the_embedder_the_app_loads_is_the_one_the_floors_were_measured_under():
    """El embedder en runtime es el que produjo las cifras publicadas.

    La aserción es una igualdad entre dos valores que ya existen en el
    repositorio: lo que se ejecuta, resuelto desde ``.env`` o, en su ausencia,
    desde el default de ``config.py``; y lo que ``tests/real_wiki.py`` declara
    como embedder de los floors. Ninguno de los dos está escrito aquí.
    """
    effective, origin = _effective()

    assert effective == FLOOR_EMBEDDER, (
        f"la aplicación carga el embedder {effective!r} (de {origin}), y todas las "
        f"cifras de retrieval que publica este repositorio se midieron bajo "
        f"{FLOOR_EMBEDDER!r} (tests/real_wiki.py::FLOOR_SET_BY: {FLOOR_SET_BY!r}).\n\n"
        f"{origin} es el valor que gana en runtime: backend/main.py:39 carga .env "
        "antes de que backend/config.py se importe en backend/main.py:46, y "
        "backend/config.py:278 construye Config() en ese import. README.md:129 "
        "publica 0.653 -> 0.796 para el cambio del modelo inglés a este, así que la "
        "configuración que está corriendo ahora es la de 0.653, la que esa fila dice "
        "que se sustituyó.\n\n"
        f"Corrige el valor en {origin}. Si lo que está en curso es re-medir los "
        "floors bajo otro embedder, entonces cambia "
        "tests/real_wiki.py::EMBEDDING_MODEL y vuelve a medir: no relajees esta "
        "aserción."
    )


def test_the_live_env_and_the_template_agree_on_the_embedder():
    """``.env`` y ``.env.example`` no divergen en la clave de medición.

    Sin este salto, la separación entre el fichero vivo y la plantilla que lo
    documenta es invisible: ambos se corrigen por separado, la aplicación carga
    el valor del vivo, y la plantilla sigue anunciando otro. La primera
    comprobación de este módulo lo detecta mientras alguien está mirando; este
    detecta que la fuente de la que se copió se separó.
    """
    if not ENV_FILE.is_file():
        pytest.skip(
            f"{ENV_FILE.name} no existe en este checkout. Está en .gitignore:21, así "
            "que un clon limpio no lo tiene y no hay fichero vivo contra el que "
            "comparar la plantilla: no comparar no es coincidir. La otra comprobación "
            "de este módulo sigue ejecutándose, por la rama del default, y es la que "
            "tiene dientes: dice que el valor que la aplicación cargaría es el medido."
        )

    template = _template()
    expected = _model_name(template, EMBEDDING_KEY)
    effective, origin = _effective()

    declared = _declarations(ENV_FILE).get(EMBEDDING_KEY, [])
    forms = (
        ", ".join(
            f"{declaration.form} en {declaration.site} ({declaration.value!r})"
            for declaration in declared
        )
        or "nada en absoluto"
    )

    assert effective == expected, (
        f"{ENV_FILE.name} declara {EMBEDDING_KEY} así: {forms}. "
        f"{ENV_EXAMPLE.name} declara el valor de envío {expected!r} en "
        f"{template.site}. El valor en vigor es {effective!r}, de {origin}.\n\n"
        "Un operador que copia la plantilla espera ese valor, y el fichero vivo le "
        "da otro. O se corrige el vivo para que diga lo que dice la plantilla, o se "
        "cambia la plantilla -- pero no se dejan los dos separados, porque el que "
        "gana es el vivo y la documentación no lo dice."
    )