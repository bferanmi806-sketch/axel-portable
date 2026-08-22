# Lightweight GitHub Reuse Scan

The scan prevents unnecessary framework invention without turning every plan into a research project.

## Search

Use `gh search repos` with three to five query variants covering the problem, common category names, and likely implementation language. Search repository names, descriptions, topics, and README content where useful.

Prefer original repositories over forks and aggregators. Treat stars as discovery evidence, not quality proof.

## Shortlist

Inspect no more than five credible candidates. For each candidate, use `gh repo view`, the README, license, releases, recent commits, issue activity, and security information that is publicly available.

Capture:

- Repository and URL
- What it already solves
- Functional gaps
- License compatibility
- Maintenance and release signals
- Documentation quality
- Community or production-adoption evidence
- Security and trust implications
- Integration and migration effort
- Extension seams and lock-in

## Depth control

- If the first search variants yield no credible candidate, record that and stop the scan.
- If one candidate is clearly dominant, inspect it deeply enough to verify fit and stop broad searching.
- If several candidates are close, compare the strongest three.
- Use deeper `research` only when a candidate could materially change the architecture or delivery effort.

## Decision standard

Choose:

- **Adopt directly** when the repository satisfies the goal with configuration or ordinary integration.
- **Adopt with adapters** when its core is suitable but the project needs a narrow seam around it.
- **Fork and modify** only when license, maintenance burden, and divergence risk are acceptable.
- **Combine existing projects** when their responsibilities compose cleanly without creating a fragile stack.
- **Build custom** when evidence shows no candidate meets load-bearing requirements at acceptable adoption cost.

Never recommend a repository from its description alone. Never reject reuse merely because custom code feels easier to imagine.
