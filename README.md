# GenAI_Archival_MetaData

Extract structured **metadata** from free-flow, unstructured text according to a **defined schema** using generative AI.

The goal is to build structured data from **loosely-formatted prose** — the kind of descriptive text that archivists, curators, and reviewers write by hand. We are exploring how well generative AI models can reliably populate a schema from such text across different domains and document types. Initial development uses **film reviews** as a convenient, richly-described corpus with known ground-truth values; the approach is intended to generalize to other archival and preservation contexts.

## How it works

The pipeline takes unstructured text in and produces schema-conformant structured records out:

```text
unstructured text  ──▶  extractor  ──▶  structured metadata (schema-validated)
   (review prose,         (GenAI +          (e.g. schema.org-style
    archival notes,        schema-           Movie / Review / archival
    curatorial text)       driven)           records)
```

A target schema defines the fields to populate. The extractor reads the prose and emits a record conforming to that schema, which can then be validated against known-good values. The same pipeline design is intended to work across different corpora by swapping schemas and ground-truth data.

## Corpora

We are starting with one well-characterized corpus and expect to add others:

### Initial corpus: Nitrate Online film reviews

Development and evaluation begin with an archive of ~2,000 film reviews from **[nitrateonline.com](https://nitrateonline.com)**, written by Carrie Gorringe and owned by the project authors.

- Source repository: [`ScottThurlow/nitrateonline`](https://github.com/ScottThurlow/nitrateonline)
- Format: HTML files organized by year (e.g. `1996/rcasino.html`); `r`-prefixed files are reviews, `f`-prefixed files are festival coverage.
- Each review is free-flow prose. A number of files also embed **schema.org `Review` / `Movie` JSON-LD** (title, year, director, actors, production company, poster image), which serves as a useful **ground-truth target** for measuring extraction accuracy.

### Future corpora

Additional domains — such as other archival or preservation contexts — will be evaluated as the approach matures. Candidates will be documented here as they are identified.

## Repository layout

Each pilot project lives in its own top-level folder, with its own README, schema, extractor, and evaluation material:

| Folder | Purpose |
| --- | --- |
| [`MovieReviewPrototype/`](MovieReviewPrototype/) | Pilot: metadata from film reviews (Nitrate Online) |
| [`CopyrightRecords/`](CopyrightRecords/) | Pilot: metadata from copyright records |
| [`slr/`](slr/) | Systematic Literature Review (not a pilot) |

## Systematic Literature Review (SLR)

Alongside the practical extraction work, we are conducting a **Systematic Literature Review** on the use of generative AI for archival and metadata extraction tasks. The SLR materials (protocol, search strategy, screening results, synthesis) are maintained in this repository. The SLR informs both the design of the extraction pipeline and the evaluation methodology.

SLR materials will be added to a dedicated folder (`slr/`) as the review progresses.

## Status

Early setup. The repository currently contains project scaffolding (license, code ownership, contribution rules). Schema definitions, the extractor, evaluation tooling, and SLR materials are to follow.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). In short: the code owners (Carrie Gorringe and Scott Thurlow) commit directly; everyone else opens a pull request that must be approved by a code owner.

## License

© Carrie Gorringe and Scott Thurlow. Licensed under
**[Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International (CC BY-NC-ND 4.0)](https://creativecommons.org/licenses/by-nc-nd/4.0/)** — see [LICENSE](LICENSE).

This is the "don't reuse" license: you may view and share the material **with attribution**, but **no commercial use** and **no derivative/modified versions** may be distributed without permission. The Nitrate Online review texts remain the property of their author and are included for research and evaluation only.
