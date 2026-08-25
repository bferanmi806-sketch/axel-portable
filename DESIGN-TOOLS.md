# Axel Global Design Tooling

This manifest records the clean global OpenCode design integrations. A separate
local farming experiment remains outside this repository and is not a runtime
dependency.

## Installed

- Interface Design: `Dammyjay93/interface-design`, commit `2f9be3206855bcb2d1d0af262c8bae25cba6658d`, MIT. Primary craft-first product/interface design skill for dashboards, tools, and data interfaces.
- Impeccable: `pbakaus/impeccable`, release `skill-v4.1.1`, commit `5a149f3fdb1b5793f10567233b1dcab98fc305fd`, Apache-2.0. Critique, polish, and bounded quality layer; not the primary design brain.
- OpenAI Product Design: `openai/role-specific-plugins`, Product Design plugin `0.1.50`, commit `fe5608d2512a7d6a7b9821ce8a88c48464ecd6e4`, MIT. Promoted skills include the `index` router plus `audit`, `design-qa`, `get-context`, `ideate`, `share`, `url-to-code`, and `user-context`; the bundled `image-to-code` route remains distinct from Axel's existing local skill.
- UI/UX Pro Max: `nextlevelbuilder/ui-ux-pro-max-skill`, release `v2.15.0`, commit `a38d04c3d5c298c851dbe5e6ee1965ee3de42cb5`, MIT. Optional on-demand design research and reference skill.

## Existing And Not Duplicated

- Axel's frontend routing, design taste, existing-project redesign, frontend visual review, image-to-code, minimalist, high-end, industrial, brand, and image-reference skills remain in place.
- Axel's global `.agents/skills/research` was preserved instead of installing the Product Design skill with the same name.
- Axel's local `image-to-code` skill was preserved instead of installing the Product Design skill with the same name.
- Impeccable `4.0.3` remains in the Codex runtime as a separate older runtime-scoped copy; the OpenCode global install is the newer `4.1.1` copy.
- No Mobbin, 21st.dev, new MCP, browser, screenshot, node_modules, repository clone, or experiment artifact was added as a global dependency.

## Workflow

For UI work: understand the existing product and design system, research real
references when useful, establish one direction, implement with existing
components, render in Axel's available browser, invoke the read-only
`design-review` critic, make one focused repair pass, capture final screenshots,
and leave human review as the approval gate. Existing product language and
components outrank external patterns.

## Design Reference Routing

External references are research inputs, not templates to clone. For mature
applications, inspect the product's current screens, components, tokens, styles,
and nearby flows first. Existing product design always takes priority over
external inspiration.

Use the following free-reference routing when external research is useful:

- Website or new visual direction: `Refero Styles` -> `Seesaw` -> `Inspora`
- AI assistant, agent, or chat interface: `Beautiful UI` -> relevant real AI products -> `Refero Styles`
- Motion, interaction, or micro-interactions: `60fps public gallery`
- Mobile-first composition: `Loadmo.re`
- Fresh experimental web or product direction: `Inspora`
- Footer-specific design: `Footer.design`
- Social or launch graphics: `posts.design` or `OGFolio`
- Presentation or deck design: `Deck Gallery`

When using references:

1. Inspect several relevant examples rather than copying one.
2. Identify transferable principles: hierarchy, layout, density, spacing, typography, surfaces, interaction patterns, motion, and component treatment.
3. Adapt those principles to the project's existing visual language and reuse its components before introducing external ones.
4. Preserve project-specific design decisions in the appropriate design context file where useful.

Do not use paid services when a suitable free reference source exists. Do not
add Mobbin, new MCPs, browser or screenshot tooling, or 21st.dev as a mandatory
dependency.

## Design Studio / Product Design Workflow

The OpenAI Product Design package supplies the source-grounded design workflow:
load saved context, pass through `get-context`, then route to `ideate`,
`url-to-code`, or `image-to-code` as appropriate. Use `design-qa` after a
source-grounded prototype and `share` only when a deployment target is chosen.
The browser mapping is adapted for Axel's connected Paseo browser and local
preview flow. Do not scaffold a new visual build without a selected visual
target.

## Existing Image-to-Code Capability

- Portable path: `opencode/skills/local/image-to-code/SKILL.md`
- Upstream/source: not identified. The skill has no upstream repository, version, or license metadata in its frontmatter or local documentation; treat it as an Axel-local capability.
- Invocation: OpenCode discovers it as `image-to-code`. `frontend-design-routing` routes to it when a screenshot or supplied visual reference is available. Product Design routes to an image-to-code workflow only after `get-context` and selection of a visual target.
- Inputs: screenshots, supplied visual references, or an image-first website brief. It is not a direct URL-cloning tool and does not define a browser capture command. For a live URL, use Product Design `url-to-code` or capture the page with Axel's existing browser first, then provide the resulting screenshot.
- Relationship to other capabilities: use Interface Design for the primary product/interface direction and existing-system preservation; use Product Design for source-grounded visual exploration or faithful translation; use Impeccable for bounded critique/polish after implementation; use `design-review` for an independent rendered evaluation whose fixes remain with the builder.
- Limitations: it is strongly image-first and requires image generation before coding when generation is available; it can over-index toward premium/art-directed website work; it does not replace existing-product inspection, live browser verification, accessibility review, or the read-only design critic; and its lack of known upstream provenance makes it less auditable than the promoted upstream skills.
