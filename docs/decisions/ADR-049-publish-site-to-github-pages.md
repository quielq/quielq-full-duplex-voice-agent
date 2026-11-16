# ADR-049: Publish a workspace site to GitHub Pages, and open the link

Status: accepted. Quiel approved building `publish_site` on Nov 7, 2026, and the open step and a live test on Nov 16, 2026. Sources: https://docs.github.com/en/rest/git (Git Data API), https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site.

## Context

In the class demo the professor wanted to see the device do software development: build a website, publish it to `github.io`, and open the link. The agent could build a site and open the local file (ADR-026), but it could not publish. The sandbox has no network, and `git push`, `gh` and `curl` are refused in `bash` (ADR-034), which is right: a shell with a token and a network could push anything anywhere.

## Decision

Two tools on the workspace server.

`publish_site(folder, site)` sends one workspace folder to one GitHub Pages repo.

- The server makes the calls itself, through the Git Data API, as one commit: blobs, a tree on top of the current one, a commit, and a ref update that is never forced. No command runs, and `bash` stays as it was.
- The repo comes from `ME3_PAGES_REPO` in `.env`, never from the model. The token is fine-grained, reaches only that repo with Contents read and write, and is passed only to the workspace server (`harness/mcp_tools.py`). It never reaches a command, another server, a log, or an error message.
- Only web files go out (`PAGES_FILES`). Hidden files, symlinks and everything else stay behind, so `plan.md`, `notes.md` and `.versions` are never public. Limits: 200 files, 10 MB a file, 25 MB a site, and an `index.html` at the top.
- The site name is lower-case letters, digits and hyphens. A publish replaces that name's folder and touches nothing else in the repo.
- It is destructive and open world, so the harness reads back the real folder and name, waits for a spoken yes, then the undo window. Containment locks it with the other tools that leave the device.
- It is a mock unless `ME3_TOOL_STAGES=pages=github`. Each publish is recorded in the cost ledger at a notional price, so the `github_pages` daily cap is a limit of 20 publishes. A cap of zero refuses.

`open_published_site(site)` opens the published link in the browser.

- It takes a site name, never an address. The server looks the name up in `data/published_sites.json`, which only a real publish writes. A name that was never published is refused.
- GitHub Pages takes about a minute to build, so the server asks for the page up to six times, three seconds apart, and opens it only after a 200. Otherwise it says the site is still building and opens nothing.
- It uses the same opener as `open_in_browser`: an argument list, no shell, and only the variables the desktop needs.
- It asks for a spoken yes from the real name and is a mock unless `ME3_WORKSPACE_LIVE=1`.

## Alternatives

- Allow `git push` in `bash` with network: the token would sit in a shell the model writes commands for, and the sandbox would need network for every command.
- Widen `open_in_browser` to take an address: any page on the web could then be opened by a steered model. ADR-026 rules this out without a new decision, and a name lookup is narrower.
- Let the model pass the repo: one bad argument would publish to the wrong place. One repo in `.env` cannot be talked into another.
- Open the link without checking: the first open after a publish would often show a 404, in front of an audience.
- Skip the second yes for opening: fewer words in the demo, but it would be the only way to open a browser unasked. It stays consistent with `open_in_browser`.

## Consequences

- A published page is public at once, and the read-back names the folder, not what the pages say. A model steered by web text could write a bad page and ask to publish it. The yes is the only check on content.
- Taking a site down is manual: delete its folder in the repo on GitHub.
- A publish and an open are two questions and two undo windows, about 20 seconds of waiting in a demo.
- On the Pi in kiosk mode the opened page covers the robot face, with no way back by touch yet (the same limit as ADR-026).
- The wait makes plain requests to `github.io` from the workspace server, to an address the server built itself.
