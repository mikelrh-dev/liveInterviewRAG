# CONVENCIONES

Fixture-only conventions sheet. Not loaded as candidate content
(`CandidateProfile._SKIP_FILES` drops this file by name).

## Tipos admitidos

`profile`, `project`, `experience`, `skills`, `story`, `opinion`,
`decision`, `faq`. One folder per type, using the plural folder name for
the singular type.

## Ciclo de confianza

- `low` — borrador, pendiente de confirmar: no se sirve a nadie.
- `medium` — revisado pero no probado: contenido real, se sirve tal cual.
- ausente — no declarado, no es `low`: se sirve tal cual.

## Marcadores

`[TODO: ...]` marca lo que falta por escribir. El indexador lo quita
antes de servirlo; la pregunta que acompaña al marcador se conserva.
