# Running harvests on GitHub Actions

GitHub-hosted runners can reach hosts the dev container cannot (Wikimedia
Commons, Wikidata, ARASAAC, …) and run many shards in parallel.

**Use a private repository.** Shard artifacts contain local copies of
pictograms under many licenses, including non-redistributable ones.

## One-time setup (operator, on the host)

1. Create a private repository and add it as `origin`.
2. Push with `cradle push`. The container token cannot change
   `.github/workflows/`.

## Run

```sh
# 1. write a job list (metadata only) and commit it
uv run handdown export-sources commons work/jobs/commons.jsonl --status blocked-network
uv run handdown export-sources git-svg work/jobs/git-svg.jsonl          # accepted, not yet harvested

# 2. start the workflow
gh workflow run harvest -f jobs=work/jobs/commons.jsonl -f adapter=commons -f shards=8

# 3. merge the results
gh run download <run-id> --dir work/shards
uv run handdown import-shard work/shards/*/*.tar.gz
uv run handdown dedupe && uv run handdown concepts && uv run handdown cluster && uv run handdown score
```

Each runner:
1. loads only its share of the job list (`--shard i/n`, every n-th source);
2. harvests it without the registry (`--no-registry`);
3. normalizes and measures with 4 workers;
4. uploads `shard-<adapter>-<i>.tar.gz`.

Importing a shard is idempotent: pictograms are matched by source and
original id.
