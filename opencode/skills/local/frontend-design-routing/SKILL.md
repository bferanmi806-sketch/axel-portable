---
name: frontend-design-routing
description: Use when selecting a frontend visual direction or combining design skills. Infer one primary style from the brief, audience, references, brand, and constraints, then layer compatible workflow guidance without mixing competing style systems.
---

# Frontend Design Routing

Use the existing `design-taste-frontend` skill as the general design-read foundation. Do not rewrite or duplicate its rules.

## Selection Order

1. Follow an existing official design system, brand identity, accessibility requirement, or regulated-domain constraint before choosing an aesthetic.
2. Choose the workflow based on the task: new page, existing-project redesign, image/reference translation, branding, or visual reference generation.
3. Infer one primary visual mode from the audience, product, references, tone, and content. An explicit style request overrides inference.
4. Layer at most one primary style mode with the workflow. Do not combine minimalist, high-end, brutalist, and experimental modes in the same task unless the brief explicitly requires a hybrid.
5. If the design read genuinely diverges, ask one focused question rather than activating multiple style systems.

## Style Signals

- Restrained, quiet, editorial, content-first, or utility-focused: `minimalist-ui`.
- Premium, luxury, tactile, cinematic, or agency-led: `high-end-visual-design`.
- Raw, industrial, mechanical, utilitarian, terminal, or declassified: `industrial-brutalist-ui`.
- Experimental, Awwwards-style, cinematic, and motion-heavy marketing: `gpt-taste`.
- Google Stitch workflow: `stitch-design-taste`.

## Workflow Signals

- Existing website or app: `redesign-existing-projects`.
- Screenshot or supplied visual reference: `image-to-code`.
- Brand identity or visual-world exploration: `brandkit`.
- Website or mobile visual comps: the matching image-generation skill, when image generation is available.
- Complete multi-file implementation: `full-output-enforcement` as an orthogonal output constraint.

The chosen style is a contextual mode, not a permanent preference. Preserve responsive behavior, accessibility, performance, and the project's existing technical conventions regardless of style.
