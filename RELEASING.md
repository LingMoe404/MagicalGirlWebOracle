# Releasing

Maintainer checklist for a public release. The release workflow publishes to
PyPI and Docker Hub when a `v*` tag is pushed.

## 1. Prepare the version

1. Update `version` in `pyproject.toml`.
2. Move the relevant entries in `CHANGELOG.md` under the new version and date.
3. Run the full suite and rebuild the admin bundle:

   ```bash
   python -m pytest
   cd frontend && npm ci && npm run build && cd ..
   ```

4. Commit the version/changelog/static-asset changes.

## 2. One-time publishing setup

### PyPI (trusted publishing)

1. Create the project `magicalgirl-web-oracle` on PyPI (or reserve the name).
2. In the PyPI project settings, add a **GitHub trusted publisher**:
   - Owner: `LingMoe404`
   - Repository: `MagicalGirlWebOracle`
   - Workflow: `release.yml`
   - Environment: `pypi`
3. Create the `pypi` environment in the GitHub repository settings.

No PyPI API token is stored in GitHub.

### Docker Hub

1. Create the repository `lingmoe404/magicalgirl-web-oracle`.
2. Create a Docker Hub access token with read/write permissions.
3. Add repository secrets:
   - `DOCKERHUB_USERNAME` = `lingmoe404`
   - `DOCKERHUB_TOKEN` = the access token

## 3. Tag and publish

```bash
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
```

The `Release` workflow then:

- builds the sdist and wheel and publishes to PyPI via trusted publishing;
- builds `linux/amd64` and `linux/arm64` images and pushes `0.1.0`, `0.1`, and
  `latest` tags to Docker Hub.

## 4. Verify

```bash
pip install --upgrade magicalgirl-web-oracle
python -c "import magicalgirl_web_oracle; print('ok')"
docker pull lingmoe404/magicalgirl-web-oracle:0.1.0
```

Also check:

- the GitHub Actions run for the tag is green;
- the PyPI page renders the README and license;
- the Docker Hub page shows the new tags and the description from
  `DOCKERHUB.md`;
- a fresh container answers `GET /healthz` with `{"status":"ok",...}`.

## Notes

- Do not publish from a dirty working tree.
- Do not reuse a version number. PyPI and Docker Hub are immutable for a given
  version tag.
- If a release fails after the tag is pushed, fix forward with a new patch
  version; do not move the tag.
