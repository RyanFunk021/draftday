# templates/ — the one page, and the copy.md text system

`index.html` is the only template. All user-facing copy is externalized to
`/copy.md` at the repo root, parsed by `app.py`'s `load_copy()` into a
`{slug: text}` dict keyed by `## slug` headings, hot-reloaded on file mtime
change (no restart needed to edit copy). The template receives it two ways:

- `{{ c['slug'] }}` — server-rendered text baked into the HTML at page load.
- `COPY` — the same dict serialized to JSON in a `<script>` tag, for
  `static/app.js` to reference dynamically (e.g. `COPY["sim-progress"]`,
  which contains `{done}`/`{total}` placeholders the JS fills in for
  streamed sim progress).

If you add a new piece of UI copy, add the `## slug` block to `/copy.md`
first, then reference it from either the template or `app.js` — don't
hardcode new user-facing strings in either.

Structural sections (`#setup`, `#listwrap`, `#rosterwrap`, `#simwrap`) are
`hidden` by default and revealed progressively by `app.js` as the user
completes each step (build list -> tips/board appear -> roster preview ->
sim). Keep new sections following that same reveal pattern rather than
showing everything up front.

See `static/AGENTS.md` for the JS that drives this template.
