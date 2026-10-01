# Authorization and Mutation Boundaries (v2)

Least privilege by phase. Capability availability is never authorization. **New in v2: a denied or missing authorization becomes a first-class blocker — work continues elsewhere and the request stays visible until resolved** (see [state-contract.md](state-contract.md) rule 2).

## Allowed without new confirmation

- Read files, inspect repositories, query public sources, run non-mutating diagnostics inside the user-scoped project.
- Create or edit research artifacts that directly implement the user's request.
- Initialize local git for a newly requested project; validate; scoped local commits staging explicit paths only.
- Use already-authorized local tools for read-only retrieval.

## Require a recorded project scope (in `state.authorization`)

- Create a GitHub repository, push, open or modify a PR, publish a release, change remote settings.
- Mutate an external citation library, issue tracker, document, or collaboration system.
- Send potentially sensitive source material to an authenticated third-party model UI.

Authorization for one repository, collection, or paper never transfers to another.

## Require explicit confirmation for each action

- Upload large or final data to Drive or any cloud store (`drive_upload: confirm_each_action` is enforced by the validator).
- Publish confidential, licensed, personal, regulated, or embargoed material.
- Delete, overwrite, or migrate external records irreversibly.

## The blocker protocol for denied/missing authorization

```bash
CLI blocker <root> add --type permission \
   --description "git push to origin requires user authorization" \
   --queued-action "push branch codex/xyz then open PR"
```

Then: continue other open gates; each loop tick resurfaces the blocker; on user approval, `blocker resolve` and execute the queued action verbatim. Never retry the denied action unmodified, never drop it silently.

## Authentication and browser automation

Probe authentication only when the current phase needs the service; let the user complete login/OAuth. Never request or log passwords, cookies, tokens, recovery codes, CAPTCHA solutions; never bypass quotas, rotate accounts, spoof fingerprints, or expose debug ports. Prefer purpose-built connectors/APIs over browser control; browser submissions are visible user-account actions — record immutable `run_id`s and reconcile before retrying. **Server red line: a shared compute server gets zero credentials** — public data and code only.
