# handdown

Ein Forschungskatalog von Piktogrammen aus möglichst allen auffindbaren
Quellen: Icon-Sets, Normen und Beschilderung, UK-Symbolsysteme (AAC),
Schriften und Unicode, Kartensymbole. Jedes Piktogramm wird mit Quelle und
Metadaten erfasst, nach **Bedeutung** und **Darstellung** gruppiert und danach
bewertet, wie gut es als kleines, rein schwarz-weißes Symbol funktioniert –
als Ideengrundlage für ein späteres, einheitliches Piktogramm-Set (ggf. als
Schrift).

**KI-Offenlegung**
- Agent: Claude Opus 5.5 (`claude-opus-5-5`) von Anthropic, über Claude Code –
  Code, Dokumentation und Quellenrecherche in Zusammenarbeit mit dem Autor;
  ca. 0,3 Mio. Tokens Kontext in der ersten Sitzung (wird je Sitzung
  aktualisiert).
- Autor: Designentscheidungen, Anforderungen, Review.

## Funktionen

- **Sammeln**: Adapter für Iconify (~240 Sets via npm), GitHub-Repositories
  und npm-Pakete; jede Plattform, Quelle, Suchanfrage und „gefunden über“-
  Verknüpfung wird protokolliert.
- **Entdecken**: Schneeballsuche – GitHub- und npm-Suchen aus Bereichen ×
  Sprachen × Plattformvokabular, READMEs und „awesome“-Listen bekannter
  Quellen werden nach weiteren Quellen durchsucht.
- **Normalisieren**: jedes SVG wird bereinigt (Skripte, externe Verweise,
  Entities entfernt), quadratisch gemacht und rein schwarz-weiß umgesetzt;
  die Farbklasse wird erfasst.
- **Messen**: Lesbarkeit bei 8–32 px, Strichstärke, zulaufende Punzen bei
  16 px, Einfachheit, optische Balance, Rastertreue, Symmetrie, Stil.
- **Gruppieren**: Bedeutungen über WordNet (Bezeichnungen in ~30 Sprachen),
  Darstellungen per Formähnlichkeit unabhängig von Kontur- oder Flächenstil.
- **Bewerten**: Konventionsstärke, Unterscheidbarkeit, gewichteter
  Gesamtwert; manuelle Overrides haben Vorrang.
- **Durchsuchen**: Obsidian-Vault (eine Notiz je Bedeutung; `overrides:` und
  `notes:` bleiben erhalten) und statischer HTML-Katalog.

## Installation und Nutzung

Siehe [README.md](../../README.md) – die Befehle sind identisch.

Alle gesammelten Grafiken bleiben im privaten Ordner `data/`. Quellen behalten
ihre eigenen Lizenzen.

## Projekt

- Lizenz: [AGPL-3.0-or-later](../../LICENSE) (nur Code)
- Design: [Spezifikation](../superpowers/specs/2026-09-28-pictogram-catalog-design.md)
- Erkenntnisse: [dev/takeaways.md](../../dev/takeaways.md)
