"""El bloque de trayectoria estructurado y la regla de abstención por niveles.

QUÉ NO HACE ESTE ARCHIVO, Y POR QUÉ ES LA PRIMERA SECCIÓN DEL DOCSTRING
-----------------------------------------------------------------------
No llama al LLM. Ni una vez, ni aunque el otro modulo diga que sí. Todas las
aserciones son sobre el STRING que ``build_system_prompt`` devuelve, que es
donde vive la decisión de este trabajo: si el bloque entra o no, qué dice la
regla cuando está y qué dice cuando no. Cómo responde el modelo a ese prompt es
una propiedad del modelo, y affirmar sobre ella aquí sería fabricar una
medición que nadie ha hecho. La persona que prueba el LLM en vivo lo hace ella;
este archivo comprueba que le hemos dado bien la pregunta.

POR QUÉ LAS ASERCIONES SON SOBRE STRINGS Y NO SOBRE UNA CONVERSACIÓN
----------------------------------------------------------------------
Porque una conversación sólo se puede "comprobar" haciendo una llamada de pago, y
un test que depende de una llamada de pago no es un test: es un gasto con
aserciones. Lo que sí es una propiedad del código es la ÍNTEGA del texto. El
contrato que este archivo fija es: "dadas estas entradas, la palabra Mercadona
está en el prompt". Es verificable sin red, sin claves y sin coste, y si algún
día el modelo deja de responder bien, el primer sitio donde mirar es si el texto
sigue llegando -- que es lo que esto mide.

LOS EMPLEOS, Y POR QUÉ VIENEN DEL WIKI Y NO DE AQUÍ
----------------------------------------------------
``THE_THREE_EMPLEMENTS`` nombra los tres empleos que el bloque tiene que
contener. Sus valores NO están escritos en este archivo: se leen del propio
``wiki/`` con un lector de la sección ``## Career timeline (corrected)``, y se
COMPRUEBA que ese lector y el compilador dicen lo mismo. La razón es que un test
que repite a mano lo que el código produce sólo verifica que la copia no se ha
desincronizado de sí misma: si el dueño edita la línea del wiki, el test pasa y
el bloque dice otra cosa. Leído del wiki, editar el wiki rompe el test, que es
lo único que un guard debe hacer.

Y ``test_the_block_names_no_employer_the_wiki_does_not`` va al revés: todo
empleador que el bloque afirma tiene que aparecer LITERALMENTE en ``wiki/``. Es
la afirmación de "compilar, no inventar"Convertida en aserción, y es la que
fallaría si alguien metiera un empleador en ``profile.json`` a mano.

LA DEGRADACIÓN, POR QUÉ TIENE SU PROPIA CLASE
---------------------------------------------
``get_work_history_block()`` devuelve ``""`` cuando no hay datos, y el prompt
tiene que cambiar de verdad — no sólo perder una sección. Un bloque vacío con la
misma redacción sería un prompt que promete una garantía que no tiene, y el
modo degradado se nota justamente en lo que el modelo dice cuando NO puede
contestar. Por eso los tests de aquí comprueban las dos ramas por separado y que
la prohibición fuerte de abstenerse (``IDENTITY_RULE``) NO esté en la rama
degradada: una prohibición de abstenerse sin los datos que la justifican sólo
tiene un final posible, que es inventar.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from backend.prompts.candidate import (
    DEGRADED_WORK_HISTORY_NOTICE,
    IDENTITY_RULE,
    build_system_prompt,
)
from backend.services.candidate import CandidateProfile
from tests.real_wiki import WIKI_ROOT

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPILE = REPO_ROOT / "scripts" / "wiki" / "compile.py"

#: El presupuesto de la FASE C: ~200 tokens de empleador+rol+fechas. Medido en
#: tokens de PALABRA y no del tokenizador del modelo, y por debajo del presupuesto
#: a propósito: un token de palabra es una cota INFERIOR del token real, así que
#: pasar aquí con margen es pasar de verdad en el modelo.
TOKEN_BUDGET = 200


# ── Los tres empleos, LEÍDOS del wiki ────────────────────────────────────────


def _timeline_section() -> str:
    """El cuerpo de ``## Career timeline (corrected)`` de ``wiki/profile/mikel.md``."""
    body = (WIKI_ROOT / "profile" / "mikel.md").read_text(encoding="utf-8")
    lines: list[str] = []
    inside = False
    for raw in body.splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            if stripped.lower() == "## career timeline (corrected)":
                inside = True
                continue
            if inside:
                break
            continue
        if inside and stripped:
            lines.append(stripped)
    assert lines, (
        "wiki/profile/mikel.md no tiene '## Career timeline (corrected)'. "
        "Si esa sección se renombró, compile._parse_experience deja de "
        "encontrarla también y el bloque sale vacío: este test y el "
        "compilador se rompen a la vez, que es lo correcto."
    )
    return "\n".join(lines)


#: (período, rol, empresa) leídos del wiki con la misma regla de delimitado que
#: ``scripts/wiki/compile.py::_parse_experience`` (partir por la PRIMERA coma).
#: Deliberadamente NO son una transcripción: se derivan, para que editar el wiki
#: rompe el test en vez de dejarse sincronizar solo.
def _employments_from_wiki() -> list[tuple[str, str, str]]:
    out = []
    for line in _timeline_section().splitlines():
        match = re.match(r"^-\s*\*\*(.+?):\*\*\s*(.*)$", line)
        assert match, f"línea de trayectoria sin el formato PERIODO: Texto: {line!r}"
        period, remainder = match.group(1).strip(), match.group(2).strip()
        if "," in remainder:
            role, company = (part.strip() for part in remainder.split(",", 1))
        else:
            role, company = remainder, ""
        out.append((period, role, company))
    return out


THE_THREE_EMPLEMENTS = tuple(
    entry
    for entry in _employments_from_wiki()
    if entry[2] in {"Mercadona (equipos grandes)", "BM Supermercados"}
)

#: Los TRES empleos, por nombre de empresa y por rol. El conjunto se fija aquí y
#: no se deriva, porque lo que se quiere afirmar es que están los TRES, no que
#: hay los que haya: derivarlo lo volvería cierto por construcción y el test no
#: mediría nada.
EXPECTED_EMPLOYMENTS = (
    ("Mercadona", "Gerente B", "2019"),
    ("BM Supermercados", "Encargado", "2016"),
    ("BM Supermercados", "Frutero", "2015"),
)


def test_the_wiki_names_exactly_the_three_expected_employments():
    """Guardia del propio ``THE_THREE_EMPLEMENTS``: el wiki tiene los tres.

    Sin este test, ``THE_THREE_EMPLEMENTS`` podría quedarse vacío o con dos
    entradas y todo lo demás pasaría sin comprobar nada.
    """
    employers = {(company, role) for _, role, company in THE_THREE_EMPLEMENTS}
    assert employers == {("Mercadona (equipos grandes)", "Gerente B"), ("BM Supermercados", "Encargado"), ("BM Supermercados", "Frutero")}, (
        "wiki/profile/mikel.md ya no declara los tres empleos que este "
        f"archivo afirma. Encontrados: {sorted(employers)}. Si el dueño cambió "
        "la trayectoria, actualiza THE_THREE_EMPLEMENTS a mano y en un commit "
        "visible: es un cambio de hechos, no de código."
    )
    assert len(THE_THREE_EMPLEMENTS) == 3


# ── El perfil compilado de verdad, en un tmp_path ─────────────────────────────


@pytest.fixture(scope="module")
def compiled_candidate(tmp_path_factory) -> CandidateProfile:
    """``candidate/profile.json`` compilado del wiki REAL, en un directorio temporal.

    Se compila en vez de leer ``candidate/`` porque ese directorio NO está en git
    (``git ls-files candidate`` devuelve 0 ficheros): un test que lo leyera
    pasaría en la máquina del autor y fallaría en un clon limpio, que es la
    forma exacta del defecto que ``tests/real_wiki.py`` existe para impedir.

    El wiki sí está en git, así que esta ruta es hermética en cualquier clon y
    además ejercita la cadena COMPLETA -- ``compile.py`` incluido, que es donde
    los datos se vuelven estructurados.
    """
    out = tmp_path_factory.mktemp("candidate")
    result = subprocess.run(
        [sys.executable, str(COMPILE), "--wiki", str(WIKI_ROOT), "--out", str(out)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"compile.py falló sobre el wiki real (exit {result.returncode}):\n"
        f"{result.stdout}\n{result.stderr}"
    )
    profile = CandidateProfile(out, wiki_dir=WIKI_ROOT)
    profile.load()
    return profile


# ── El renderizador ──────────────────────────────────────────────────────────


def _profile_with(tmp_path: Path, payload) -> CandidateProfile:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "profile.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    profile = CandidateProfile(candidate)
    profile.load()
    return profile


class TestTheRendererReadsStructuredDataOnly:
    """``get_work_history_block``: qué imprime y qué se niega a imprimir."""

    def test_one_line_carries_period_role_and_company(self, tmp_path):
        profile = _profile_with(
            tmp_path, {"experience": [{"period": "2019", "role": "Gerente", "company": "X"}]}
        )
        assert profile.get_work_history_block() == "2019 — Gerente, X"

    def test_an_entry_without_a_company_still_renders_its_period_and_role(self, tmp_path):
        """Una entrada sin empresa sigue siendo una fecha y un puesto.

        ``compile.py`` deja ``company`` vacío cuando la línea del wiki no trae
        coma (``compile.py:97-98``), y esas líneas son las de transición. Que
        salgan es lo honesto: el periodo y el puesto existen aunque no haya
        empleador, y filtrarlas dejaría huecos en una trayectoria que sí los
        tiene.
        """
        profile = _profile_with(
            tmp_path, {"experience": [{"period": "Nov 2025", "role": "Dejó Mercadona"}]}
        )
        assert profile.get_work_history_block() == "Nov 2025 — Dejó Mercadona"

    def test_no_profile_data_yields_no_block(self, tmp_path):
        """Sin ``profile.json`` el bloque es ``""``, no un bloque parcial."""
        profile = CandidateProfile(tmp_path / "nada")
        profile.load()
        assert profile.get_work_history_block() == ""

    def test_an_empty_experience_list_yields_no_block(self, tmp_path):
        profile = _profile_with(tmp_path, {"name": "X", "experience": []})
        assert profile.get_work_history_block() == ""

    def test_entries_without_period_or_role_are_dropped(self, tmp_path):
        """Una entrada sin periodo o sin puesto no es un hecho de identidad.

        Se descarta POR ENTRADA, no en bloque: el resto de la trayectoria sigue
        saliendo. Lo que no puede pasar es que una entrada vacía produzca una
        línea con un guion y nada más, que es ruido con forma de dato.
        """
        profile = _profile_with(
            tmp_path,
            {
                "experience": [
                    {"period": "2019", "role": "Gerente", "company": "Mercadona"},
                    {"period": "", "role": "Sin fecha", "company": "X"},
                    {"period": "2020", "role": "", "company": "Y"},
                ]
            },
        )
        assert profile.get_work_history_block() == "2019 — Gerente, Mercadona"

    def test_a_non_dict_entry_costs_one_line_and_not_the_turn(self, tmp_path):
        """``profile.json`` editado a mano no puede tumbar un turno.

        El loader acepta cualquier JSON válido sin validar la forma
        (``candidate.py:50-54``), así que una entrada que sea una cadena en vez
        de un objeto es representable. Que se salte esa línea y conserve las
        demás es la diferencia entre un profile.json con un error y un turno sin
        respuesta.
        """
        profile = _profile_with(
            tmp_path,
            {
                "experience": [
                    "esto no es un objeto",
                    {"period": "2019", "role": "Gerente", "company": "Mercadona"},
                ]
            },
        )
        assert profile.get_work_history_block() == "2019 — Gerente, Mercadona"

    def test_highlights_are_not_rendered(self, tmp_path):
        """``highlights`` es prosa y el bloque tiene un presupuesto de tokens.

        El budget de la FASE C es CZIENTOS de tokens de empleador, rol y fechas.
        Los highlights son frases completas, y si se colaran el bloque dejaría de
        ser un índice para ser un documento -- que es exactamente lo que hace
        ``get_context_string`` y lo que este método existe para no hacer.
        """
        profile = _profile_with(
            tmp_path,
            {
                "experience": [
                    {
                        "period": "2019",
                        "role": "Gerente",
                        "company": "Mercadona",
                        "highlights": ["Reduje la merma un 40% en tres meses"],
                    }
                ]
            },
        )
        block = profile.get_work_history_block()
        assert "40%" not in block
        assert block == "2019 — Gerente, Mercadona"


# ── compile.py → el bloque, contra el wiki real ──────────────────────────────


class TestTheBlockIsCompiledNotInvented:
    """La cadena real: el wiki pasa por ``compile.py`` y sale como bloque."""

    def test_the_three_employments_reach_the_block(self, compiled_candidate):
        block = compiled_candidate.get_work_history_block()
        assert block, (
            "el bloque salió vacío con un candidate/profile.json recién compilado "
            "del wiki real. Si esto falla, el culprits es compile.py o el estado "
            "del profile.json, no el renderizador."
        )
        for company, role, period_start in EXPECTED_EMPLOYMENTS:
            assert company in block, f"falta el empleador {company!r} en:\n{block}"
            assert role in block, f"falta el puesto {role!r} en:\n{block}"
            assert period_start in block, f"falta el periodo de {role!r} en:\n{block}"

    def test_the_block_agrees_with_the_timeline_it_was_compiled_from(self, compiled_candidate):
        """Cada línea del bloque tiene su línea en el wiki.

        Es la versión machine-checkable de "compilar, no inventar" para el
        TEXTO: si el renderizador añadiera, reordenara o parafraseara algo, el
        número de líneas y su contenido no coincidirían con la sección que
        ``_parse_experience`` leyó.
        """
        block = compiled_candidate.get_work_history_block()
        rendered = [line.strip() for line in block.splitlines()]
        assert len(rendered) == len(_timeline_section().splitlines()), (
            "el bloque tiene un número de líneas distinto del de la sección de "
            f"trayectoria: {len(rendered)} vs "
            f"{len(_timeline_section().splitlines())}"
        )

    def test_the_block_names_no_employer_the_wiki_does_not(self, compiled_candidate):
        """Cada tokEN DE EMPRESA del bloque aparece literal en el wiki.

        La afirmación fuerte de "compilar, no inventar": si alguien añadiera una
        empresa a ``profile.json`` a mano, este test falla. Se comparan tokens en
        mayúsculas de la línea de bloqueo contra el texto del wiki entero, así
        que un nombre inventado no tiene de dónde salir.
        """
        wiki_text = (WIKI_ROOT / "profile" / "mikel.md").read_text(encoding="utf-8")
        wiki_upper = wiki_text.upper()
        for company, _role, _period in _employments_from_wiki():
            if not company:
                continue
            for token in re.findall(r"[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑ]*", company.upper()):
                if len(token) < 3:
                    continue
                assert token in wiki_upper, (
                    f"el bloque afirma el token {token!r} (de {company!r}) y esa "
                    "cadena no aparece en wiki/profile/mikel.md"
                )

    def test_the_block_is_inside_the_token_budget(self, compiled_candidate):
        """~200 tokens, medidos como PALABRAS, que es la cota inferior.

        Un token de palabra subestima el token real en español (~1.3x), así que
        un bloque que pasa con margen a nivel de palabra pasa en el modelo. La
        cifra sale de medir, no de suponerla: el bloque completo del wiki real
        son seis entradas de la trayectoria completa.
        """
        block = compiled_candidate.get_work_history_block()
        words = len(block.split())
        assert words <= TOKEN_BUDGET, (
            f"el bloque ocupa {words} palabras, por encima del presupuesto de "
            f"{TOKEN_BUDGET}. El presupuesto es de tokens del modelo, y una "
            "palabra es menos que un token, así que pasar aquí es holgado; "
            "fallar aquí es imposible sin haber roto el presupuesto de verdad."
        )

    def test_the_whole_block_is_a_handful_of_lines(self, compiled_candidate):
        """Guardia de forma: un bloque de trayectoria es corto por construcción.

        Si esto falla, el problema no es el presupuesto sino que el bloque ha
        dejado de ser un índice de la trayectoria y se ha convertido en un
        documento, que fue el defecto que motivó el método.
        """
        lines = compiled_candidate.get_work_history_block().splitlines()
        assert 1 <= len(lines) <= 12, f"{len(lines)} líneas en el bloque:\n" + "\n".join(lines)


# ── El prompt: el bloque entra, y entra aunque el retrieval no devuelva nada ──


class TestTheBlockReachesThePrompt:
    """``build_system_prompt``: el bloque es independiente del RAG."""

    def test_with_empty_retrieval_the_prompt_still_carries_the_three_employments(
        self, compiled_candidate
    ):
        """LA prueba central de la FASE C.

        ``retrieved_context=""`` es el peor caso real: el filtro de coseno
        descartó todo y el modelo recibe un turno sin ninguna página. Ése es
        exactamente el turno en el que una respuesta sobre "¿dónde has
        trabajado?" tiene que ser un "no lo tengo a mano", y por eso la
        garantía tiene que venir de otro sitio. Con el bloque presente, las tres
        empresas están en el prompt y la pregunta de identidad tiene dónde
        responderse sin depender del buscador.
        """
        prompt = build_system_prompt("", work_history=compiled_candidate.get_work_history_block())
        for company, role, _period in EXPECTED_EMPLOYMENTS:
            assert company in prompt
            assert role in prompt

    def test_the_block_survives_a_retrieval_that_found_the_wrong_pages(
        self, compiled_candidate
    ):
        """El retrieval equivocado NO puede sacar el bloque.

        Las tres páginas que salen primero para estas preguntas son páginas de
        opinión (``tests/work_history_cases.py`` lo mide). El bloque se inyecta
        igualmente: es fijo, no compite con el RAG y no se descarta cuando el
        buscador falla.
        """
        wrong = "faq/por-que-esta-empresa.md\nfaq/donde-veo-en-3-5-anos.md"
        prompt = build_system_prompt(wrong, work_history=compiled_candidate.get_work_history_block())
        assert "Mercadona" in prompt
        assert wrong in prompt, "el contexto recuperado también tiene que estar"

    def test_the_block_is_labelled_as_fixed_and_not_as_retrieved(self, compiled_candidate):
        """El encabezado dice "DATOS FIJOS", no "información relevante".

        Si el bloque se presentara como contexto recuperado competiría con las
        tres páginas del RAG en el mismo rango de atención, y el modelo podría
        descartarlo igual que descarta una página que no le sirve. Es la
        diferencia entre un dato y una pista.
        """
        prompt = build_system_prompt("", work_history=compiled_candidate.get_work_history_block())
        assert "DATOS FIJOS" in prompt
        assert "información relevante" not in prompt.lower() or "datos fijos" in prompt.lower()

    def test_the_block_comes_before_the_retrieved_context(self, compiled_candidate):
        """El orden es el argumento: primero lo que es verdad, después lo que se recuperó.

        Si el RAG fuera primero, el bloque leería como un anexo de la tercera
        página en vez de como la referencia contra la que se lee. El orden se
        afirma porque es una decisión, no un detalle de formato.
        """
        prompt = build_system_prompt(
            "PAGINA RECUPERADA", work_history=compiled_candidate.get_work_history_block()
        )
        assert prompt.index("DATOS FIJOS") < prompt.index("PAGINA RECUPERADA")

    def test_the_prompt_is_unchanged_when_nothing_is_supplied(self):
        """Sin bloque y sin retrieval, el prompt es el que había antes.

        La firma nueva tiene un valor por defecto, y un valor por defecto que
        cambiase el prompt sería una migración silenciosa de todos los llamadores
        que no lo pasan.
        """
        prompt = build_system_prompt()
        assert "Mikel" in prompt
        assert compiled_block_absent(prompt)


def compiled_block_absent(prompt: str) -> bool:
    """``True`` cuando el prompt NO lleva bloque de trayectoria.

    El nombre es una aserción disfrazada de función porque se lee en el test
    como una frase; el cuerpo es lo mínimo.
    """
    return "DATOS FIJOS" not in prompt


# ── La degradación ───────────────────────────────────────────────────────────


class TestWithoutTheBlockThePromptDegradesHonestly:
    """Sin datos, el prompt cambia DE VERDAD y no promete nada que no tiene."""

    def test_an_empty_block_does_not_break_the_prompt(self):
        """``""`` es un caso de primera clase, no un error.

        ``profile.json`` no está en git, así que en un clon recién hecho este es
        el caso POR DEFECTO y no un borde. Si launching break here, el primer
        ``docker compose up`` de alguien se cae en el primer turno.
        """
        prompt = build_system_prompt("", work_history="")
        assert "Mikel" in prompt
        assert "primera persona" in prompt

    def test_the_degraded_prompt_says_the_block_is_missing(self):
        """El aviso tiene que existir y decir qué falta.

        Sin el aviso, la ausencia del bloque es indistinguible de una pregunta
        sobre la que no hay nada, y el modelo vuelve a la abstención genérica
        por la razón que este trabajo vino a quitar.
        """
        prompt = build_system_prompt("", work_history="")
        assert DEGRADED_WORK_HISTORY_NOTICE in prompt
        assert compiled_block_absent(prompt)

    def test_the_degraded_prompt_offers_a_way_forward_instead_of_a_dead_end(self):
        """La FASE D pide desviar a proyectos, no cerrar en seco.

        "No lo tengo a mano" es honesto y es un callejón sin salida en una
        entrevista. Lo que se comprueba aquí es que el texto ofrece el desvío Y
        que sigue prohibiendo inventar: honesto y útil no es lo mismo que
        compliant.
        """
        notice = DEGRADED_WORK_HISTORY_NOTICE
        assert "proyecto" in notice.lower(), "el aviso debe ofrecer los proyectos"
        assert "No inventes" in notice, "el aviso debe seguir prohibiendo inventar"
        assert "no lo tengo a mano" in notice.lower(), (
            "el aviso debe nombrar la frase que quiere evitar, o el modelo no "
            "sabe cuál es"
        )

    def test_the_strong_identity_prohibition_is_absent_when_there_is_no_block(self, compiled_candidate):
        """Éste es el test que hace que la degradación sea una DEGRADACIÓN.

        ``IDENTITY_RULE`` dice "NUNCA digas que no tienes ese dato a mano" y es
        lo correcto CUANDO el bloque está. Sin el bloque, la misma frase es una
        invitación a inventar el empleador: la única forma de no abstenerse es
        fabricarlo. Por eso la prohibición fuerte viaja pegada al bloque y no en
        el prompt base.
        """
        prompt = build_system_prompt("", work_history="")
        assert IDENTITY_RULE not in prompt
        assert "NUNCA digas que no tienes ese dato a mano" not in prompt

    def test_the_strong_identity_prohibition_is_present_when_there_is_a_block(
        self, compiled_candidate
    ):
        prompt = build_system_prompt("", work_history=compiled_candidate.get_work_history_block())
        assert IDENTITY_RULE in prompt

    def test_the_base_rule_is_written_conditionally(self):
        """La línea de IDENTIDAD del prompt base no afirma que el bloque exista.

        Es la diferencia entre un prompt base que es verdad en los dos modos y
        uno que sólo lo es en uno. Se comprueba sobre el TEXTO de la línea, y no
        sobre el resultado de un formato, para que un cambio de redacción que
        vuelva a hacerlo incondicional falle aquí.
        """
        prompt = build_system_prompt()
        identity_line = next(
            line for line in prompt.splitlines() if line.strip().startswith("- IDENTIDAD")
        )
        assert "si" in identity_line.lower(), (
            "la línea de IDENTIDAD del prompt base debe ser condicional "
            f"('si aparece el bloque'), no una promesa incondicional: {identity_line!r}"
        )
        assert "nunca te disculpes" not in identity_line.lower()


# ── La regla de abstención por niveles ────────────────────────────────────────


def _tier_lines(prompt: str) -> tuple[str, str]:
    """Las dos líneas de nivel de la sección de abstención, en orden."""
    lines = [line.strip() for line in prompt.splitlines()]
    identity = next(line for line in lines if line.startswith("- IDENTIDAD"))
    peripheral = next(line for line in lines if line.startswith("- PERIFÉRICOS"))
    return identity, peripheral


class TestAbstentionIsSplitByTier:
    """Identidad y periféricos son dos reglas, no una frase con comas."""

    def test_both_tiers_are_declared(self):
        prompt = build_system_prompt()
        identity, peripheral = _tier_lines(prompt)
        assert identity and peripheral

    @pytest.mark.parametrize(
        "categoria",
        ["empleadores", "puestos", "fechas de cada empleo", "dónde estudiaste"],
    )
    def test_identity_categories_are_on_the_identity_tier(self, categoria):
        """Cada dato de IDENTIDAD vive en la línea de IDENTIDAD.

        La regla anterior los mezclaba en una sola frase con las cifras
        ("dónde trabajaste, fechas, nombres, cifras"), que es la razón de que un
        "¿dónde has trabajado?" recibiera un "no lo tengo a mano": la propia
        instrucción le decía que no lo tenía.
        """
        identity, _peripheral = _tier_lines(build_system_prompt())
        assert categoria in identity, f"{categoria!r} no está en la línea de IDENTIDAD"

    @pytest.mark.parametrize(
        "categoria",
        ["métricas", "cifras de negocio", "fechas que no sean de empleo", "terceros"],
    )
    def test_peripheral_categories_are_on_the_peripheral_tier(self, categoria):
        _identity, peripheral = _tier_lines(build_system_prompt())
        assert categoria in peripheral, (
            f"{categoria!r} no está en la línea de PERIFÉRICOS"
        )

    def test_no_peripheral_category_leaked_into_the_identity_tier(self):
        """La dirección del error también es un error.

        Poner "métricas" en la línea de identidad le quitaría al modelo el
        permiso de ser honesto justo en lo que sí debe serlo, y es el fallo más
        fácil de introducir al reescribir el texto.
        """
        identity, _peripheral = _tier_lines(build_system_prompt())
        for categoria in ("métricas", "cifras de negocio"):
            assert categoria not in identity

    def test_the_peripheral_tier_still_forbids_inventing(self):
        """La honestidad de antes no se pierde al partirla en dos.

        Lo que cambia es QUÉ se abstiene, no que la abstención sea una salida
        legítima. "Inventar un empleador o un puesto es el peor error posible"
        sobrevive literalmente en la línea de periféricos.
        """
        _identity, peripheral = _tier_lines(build_system_prompt())
        assert "no los tienes a mano" in peripheral
        assert "Inventar un empleador o un puesto" in peripheral

    def test_inventing_an_employer_is_still_the_worst_error(self):
        """La prohibitions que justifica toda la sección sigue en el prompt.

        Se afirma sobre el prompt entero y no sobre una línea concreta, porque
        esta es la regla que el bloque nuevo no puede relajar: la garantia de que
        el bloque está es lo que hace la abstención por identidad innecesaria, y
        si el bloque faltara la prohibición de inventar es lo único que queda.
        """
        prompt = build_system_prompt()
        assert "Inventar un empleador o un puesto es el peor error posible" in prompt