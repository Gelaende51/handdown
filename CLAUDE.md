@.punt-labs/ethos/CLAUDE.md

## Public repository — github.com/Gelaende51/handdown

- The repository is **public**. Never commit pictograms, icons, symbol fonts or any other harvested asset, or archives that contain them (tar, zip, shards): they come under many licenses, several of which forbid redistribution. Only code, tests, docs and metadata (`sources.yaml`, job lists, license and author fields) are tracked; `/data/`, `/vault/` and `/site/` stay ignored. Add a CI check that fails when a tracked file is an image, font or archive.
- Actions artifacts of a public repository can be downloaded by any signed-in GitHub user. `harvest.yml` uploads shards with local copies of pictograms, so **do not dispatch it until every shard is encrypted before upload**: encrypt with `age` to a public key stored as a repository secret (or variable), keep the private key only on the host, and decrypt in `handdown import-shard`. Remove the "run this only in a PRIVATE repository" note once that is in place.
- Commits use `Vault51 <Gelaende51@users.noreply.github.com>` (set in the repository's git config); never commit with a personal name or email.
- The container's GitHub token has no *workflows* permission: pushes that change `.github/workflows/` are refused. Ask the operator to push those from the host with `cradle push`.
