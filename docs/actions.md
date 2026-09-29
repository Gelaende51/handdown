# Running harvests on GitHub Actions

GitHub-hosted runners can reach hosts the dev container cannot (Wikimedia
Commons, Wikidata, ARASAAC, …) and run many shards in parallel.

The repository is public, so any signed-in GitHub user can download its
Actions artifacts. The shards contain local copies of pictograms under many
licenses, so a runner uploads each shard only **age-encrypted** to your
public key. The private key never leaves the host.

## One-time setup (operator, on the host)

```sh
uv run handdown keygen ~/.config/handdown/age.key   # prints the public key (age1…)
gh variable set HANDDOWN_AGE_RECIPIENT --body "age1…" --repo Gelaende51/handdown
```

Changes under `.github/workflows/` are pushed from the host with
`cradle push`; the container token has no workflows permission.

## Run

```sh
# 1. write a job list (metadata only) and commit it
uv run handdown export-sources commons work/jobs/commons.jsonl --status blocked-network
# processing backlog: re-harvest + process sources with unmeasured pictograms
uv run handdown export-sources git-svg work/jobs/process-git-svg.jsonl --status harvested --pending

# 2. start the workflow (it refuses to start without HANDDOWN_AGE_RECIPIENT)
gh workflow run harvest -f jobs=work/jobs/commons.jsonl -f adapter=commons -f shards=8

# 3. download and merge (on the host, where the private key is)
gh run download <run-id> --dir work/shards
uv run handdown import-shard --identity ~/.config/handdown/age.key work/shards/*/*.tar.gz.age
uv run handdown dedupe && uv run handdown concepts && uv run handdown cluster && uv run handdown score
```

Each runner:
1. loads only its share of the job list (`--shard i/n`, every n-th source);
2. harvests it without the registry (`--no-registry`);
3. normalizes and measures with 4 workers;
4. encrypts the shard and deletes its plaintext data;
5. uploads `shard-<adapter>-<i>.tar.gz.age`.

`export-shard` refuses to write an unencrypted shard when `GITHUB_ACTIONS`
is set. Importing a shard is idempotent: pictograms are matched by source and
original id. The decrypted copy exists only during the import.
