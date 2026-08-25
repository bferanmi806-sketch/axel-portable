---
name: frontend-visual-review
description: Use after a usable first render of a frontend or when asked to critique, polish, refine, improve, redesign, or raise the visual quality of an existing interface. Inspect the actual rendered desktop and mobile UI, separate rendering bugs from design weaknesses, then make and verify substantive visual improvements before completion.
---

# Frontend Visual Review

This is a post-render design-review and refinement skill. It is not an initial
design-generation skill and it is not satisfied by a passing build, a clean
DOM, loaded image URLs, or a technically responsive stylesheet.

Use the companion skills in this order when they apply:

1. `frontend-design-routing` chooses the primary visual direction and workflow.
2. `design-taste-frontend` executes that direction while avoiding generic frontend patterns.
3. `frontend-visual-review` independently judges the rendered result and improves it before the UI task is considered complete.

## Activation

Activate this skill when any of the following is true:

- A substantial frontend implementation has reached its first usable render.
- The user asks to review, critique, polish, refine, improve, redesign, elevate, or make an interface higher quality.
- An existing interface already renders and the task is primarily visual or experiential.
- A screenshot, browser view, or visual reference needs to be compared against the implementation.

Do not activate for backend-only work, copy-only changes, a component that has no meaningful rendered surface yet, or a purely technical refactor with no visual behavior change.

The first successful render is always a draft for substantial UI work. Complete the review loop below before reporting completion.

## Non-Negotiable Loop

Follow every stage. Do not collapse the loop into "the build passes" or "the images load."

### 1. Understand the intent

Before judging the interface, identify:

- The page or product job, audience, and primary user action.
- The intended visual read, tone, brand, references, and constraints.
- The strongest section or moment in the current design.
- The sections, states, or breakpoints that are most likely to weaken that intent.
- Which existing design system, routing decision, or project convention must be preserved.

Write a short internal design read. Do not replace the intended direction with a personal redesign preference without a clear quality or usability reason.

### 2. Inspect the actual render

Run the project using its local workflow and inspect the live rendered interface, not only source files.

At minimum inspect:

- Desktop at a wide layout such as 1440px.
- Mobile at a narrow layout such as 390px.
- Any intermediate breakpoint where the layout changes materially.
- The initial viewport and every major section below the fold.
- At least one meaningful interaction state: menu, drawer, form, hover/focus, tab, modal, or equivalent.

Use the available browser or visual inspection tools. With OpenChamber, prefer `browser.open`, `browser.resize`, `browser.snapshot`, `browser.inspect`, `browser.scroll`, and `browser.capture` as appropriate.

If screenshot export fails, continue with the live browser and another available visual-inspection method. A failed capture is not evidence that the page is correct. Never claim final visual verification without actually inspecting the post-change rendered state.

### 3. Separate technical bugs from design weaknesses

Create two separate buckets before editing:

**Technical rendering bugs**

- Broken or missing images, fonts, icons, or assets.
- Console/runtime errors, hydration failures, or failed requests.
- Overflow, clipping, unexpected scrollbars, layout collapse, or overlapping layers.
- Incorrect responsive behavior or unusable touch targets.
- Missing focus states, unreadable contrast, missing labels, or broken interaction states.

**Design weaknesses**

- Weak hierarchy or unclear first action.
- Generic, repetitive, or template-like composition.
- Poor spacing rhythm, density, section pacing, or visual transitions.
- Typography that lacks a clear role system or has awkward wrapping.
- Imagery that is weak, repetitive, poorly cropped, mismatched, or visually subordinate.
- Color use that is muddy, inconsistent, low-contrast, or disconnected from the intended brand.
- Sections that feel like a quality drop compared with the strongest section.
- Interactions that technically work but do not feel intentional, tactile, or coherent.

Fixing the first bucket does not satisfy the second. A page can be technically correct and still fail this skill.

### 4. Perform a senior visual critique

Evaluate the complete page, not only the hero. Inspect:

- **Hierarchy:** Can a first-time visitor tell what this is, why it matters, and what to do next?
- **Composition:** Do alignment, asymmetry, focal points, image crops, and negative space create a deliberate whole?
- **Typography:** Do type roles, scale, measure, line-height, wrapping, and emphasis support the brand and reading order?
- **Spacing and rhythm:** Does the page breathe, build, pause, and transition, or does it use one repeated vertical gap everywhere?
- **Density:** Is each section carrying the right amount of information for its job?
- **Imagery:** Are images real, relevant, varied, well-cropped, and proportionate to the content they support?
- **Color and materiality:** Is the palette coherent, accessible, and used to establish hierarchy rather than decorate every surface?
- **Originality:** Does the page have a distinct visual idea, or could its structure and copy belong to any similar site?
- **Section pacing:** Does the page alternate composition types and maintain interest without filler sections?
- **Consistency:** Are radius, borders, shadows, controls, labels, icon language, and interaction states governed by a clear system?
- **Interactions:** Do states communicate feedback, hierarchy, and affordance rather than merely toggling content?
- **Responsiveness:** Does the design remain intentional on mobile, or merely collapse into a long stack?
- **Quality floor:** Do the weakest sections match the quality of the strongest section?

Identify the weakest 3-5 areas. Mark any generic repetition explicitly, such as repeated equal cards, repeated split layouts, overused eyebrows, filler copy, or a single image treatment used everywhere.

### 5. Make substantive improvements

Prioritize the weakest areas by impact on first impression, comprehension, and overall composition. Make real design changes, not only cosmetic adjustments.

Permitted and encouraged changes include:

- Restructuring, removing, combining, or reordering sections.
- Replacing a weak section layout with a different composition family.
- Rebalancing the hero, section hierarchy, image scale, or content density.
- Revising typography scale, line breaks, measure, and role relationships.
- Replacing or reframing imagery, including art direction and crop changes.
- Tightening copy that causes layout noise or weakens the visual read.
- Establishing a clearer spacing, radius, border, shadow, or control system.
- Improving responsive composition rather than only reducing widths.
- Adding or repairing meaningful loading, empty, error, focus, hover, or active states.

Preserve user-authored work and unrelated changes. Do not rewrite the entire implementation when a smaller structural change solves the problem, but do not avoid restructuring when the composition is genuinely weak.

For substantial UI work, at least one meaningful visual refinement pass is mandatory. A pass that only fixes a broken import or image URL does not count as the design-review pass; if technical bugs are found, fix them and still perform the visual critique and at least one design improvement.

### 6. Render and inspect again

After editing:

1. Re-run the narrowest relevant build, test, or typecheck.
2. Re-open the live page so the post-change state is actually loaded.
3. Inspect the same desktop, mobile, and relevant intermediate sizes.
4. Revisit the same major sections and interaction states.
5. Compare the weakest areas against the pre-change critique.

If a major weakness remains, repeat the critique and refinement loop. Do not stop after one pass merely because the code is valid. Stop only when the remaining issues are low-impact, explicitly constrained, or require user input.

## Review Record

Before reporting completion, retain a concise review record containing:

- Design intent and primary visual direction.
- Viewports and interaction states actually inspected.
- Technical bugs found and fixed.
- The weakest 3-5 design areas identified.
- Substantive design changes made.
- Post-change visual evidence and remaining limitations.

If the browser could not render, do not call the work visually verified. Report the blocker and continue by fixing the environment or ask the user for the missing visual access.

## Completion Gate

Do not consider a substantial visual frontend complete unless all are true:

- The actual post-change render was inspected at desktop and mobile sizes.
- Major sections below the fold were inspected, not only the first viewport.
- At least one meaningful interaction state was inspected.
- Technical rendering bugs were separated from design critique.
- The weakest 3-5 areas were considered and the most important were improved.
- The page was checked for generic repetition, hierarchy, composition, typography, spacing, density, imagery, color, originality, pacing, consistency, interactions, and responsive quality.
- A build or equivalent project validation passed.
- Any remaining visual limitations are clearly stated.

Never use "build passes," "responsive CSS exists," or "images load" as a substitute for this gate.
