## Summary

<!-- What changed and why? -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Documentation
- [ ] Refactor / maintenance
- [ ] Security hardening

## Verification

<!-- Commands and results. The default test suite needs no keys or network. -->

```bash
python -m pytest
```

## Checklist

- [ ] I added or updated tests for behavior changes.
- [ ] I ran the full test suite and reported the result.
- [ ] I rebuilt the admin bundle if frontend source changed.
- [ ] I did not commit `.env`, `data/`, keys, or private hosts.
- [ ] I updated `CHANGELOG.md` for user-visible changes.
- [ ] The change keeps `ALLOW_PAID_OVERAGE=false` and free-quota-only behavior.

## Notes for reviewers

<!-- Deployment implications, security notes, follow-ups. -->
