# Design Cleanup: Metric UI And Pattern Panels

Branch: `design-cleanup-metric-ui`

Base: `be2cca7` (`Merge template updates and refresh dashboard data`)

## Goal

Clean up the post-PR dashboard UI so metric selection feels coherent, active-time language is consistent, the graph behaves better, and the lower pattern panels feel like first-class dashboard controls.

## User Notes

1. Dashboard should default to `Total` tokens.
2. Metric button order should be `Total`, `New`, `Output`, `$ Cost`.
3. In cost mode, make `(est.)` much smaller.
4. `Daily Token Load` should match selection:
   - `Total Token Load`
   - `New Token Load`
   - `Output Token Load`
   - `Total Cost (est.)`
5. The plan pill shifts right in `New`; keep it aligned under the hero title.
6. Make `32x your $200.00/mo Max plan` pill smaller.
7. Replace active-time definition with:
   `Active Time Per Day: Length of time where any tool was generating tokens without a 2 hour gap.`
8. Add real hover state to Hour Of Day, like the main graph.
9. Subagent share should include more than Claude if reliable.
10. Calendar heatmap should flex gracefully to screen width.
11. Graph hover should say `Active Time`, not `Session Length`.
12. When active time is toggled off, graph should use the space better and the `Most output` record should move close to the top of the peak bar.

## Research Notes

- `app.js` currently defaults `state.metric` to `output`.
- Metric buttons are hard-coded in `index.html` as `Output`, `New`, `Total`, `$ Cost`.
- `METRIC_LABEL`, `METRIC_SHORT`, and `METRIC_UNIT` drive hero, captions, record labels, and tooltips, but the contract is scattered.
- Chart title is static markup: `Daily Token Load`.
- Plan pill lives inside `.hero-title` after owner handle; flex wrapping causes it to shift depending on hero text width.
- Tooltip still hard-codes `session length`.
- Hour histogram uses native `title` attributes only.
- Heatmap uses fixed 12px grid columns for cells and months.
- `updateSubagentShare()` sums `day.subagentUsage`, which currently only includes Claude `/subagents/`.
- Codex `read_session_meta()` detects `source.subagent`, but importer does not pass that through to `UsageEvent.subagent`.
- OpenCode has no reliable subagent signal in the current importer, so it should not be counted in subagent denominator yet.

## Product Decisions

- Subagent denominator is eligible-provider usage only: Codex + Claude Code. Exclude OpenCode until a real subagent signal exists.
- User-facing chart language should say `active time` wherever it refers to the gold line or daily active-time metric. Do not rename unrelated history/session implementation terms unless they are visible UI copy.
- Record-label collision priority:
  - Metric peak record is primary.
  - Active-time record appears only when active time is enabled.
  - If both labels would overlap, stack active-time below the metric record.
  - If stacked labels still exceed bounds, shift active-time left.
  - Hide active-time only as the last fallback.
- Browser/Chrome DevTools inspection is enough for lane visual QA. No Playwright requirement unless this becomes a release gate later.

## Visual Thesis

Keep the command-center look, but make the selected metric drive the whole surface coherently instead of feeling like a token-specific dashboard with a metric toggle bolted on.

## Interaction Thesis

Metric selection should update hero, chart title, captions, records, tooltips, and pattern panels as one system. Hover affordances should feel consistent across chart, Hour Of Day, heatmap, and history. Active-time toggle should reclaim visual emphasis for bars when disabled.

## Agent Lanes

### Lane A: Metric Mode, Hero, And Copy

Ownership:
- `index.html`
- `app.js` metric constants/text wiring
- `styles.css` hero and plan-pill styles

Tasks:
- Change default metric to `total`.
- Reorder metric buttons to `Total`, `New`, `Output`, `$ Cost`; `Total` should be the initial active button.
- Add a single `METRIC_META` contract for:
  - hero unit
  - chart title
  - range caption label
  - record label
  - tooltip label
- Add a dedicated chart title element, preferably `#chartTitle`, instead of querying a generic `h2`.
- Render chart title by metric:
  - `Total Token Load`
  - `New Token Load`
  - `Output Token Load`
  - `Total Cost (est.)`
- In cost hero, render `(est.)` as a small inline element or class so only `(est.)` shrinks.
- Restructure hero so `h1 + owner` stay on the primary line and plan pill sits on a stable row aligned to the left edge for all metrics.
- Shrink plan pill padding/font.
- Replace session definition copy with the exact active-time copy from this plan.

Lane verification:
- `node --check app.js`
- Browser inspect default load: `Total` active, title says `Total Token Load`.
- Switch all four metrics and verify hero unit, chart title, caption, and plan pill alignment.
- Verify cost `(est.)` is visibly smaller.

### Lane B: Main Chart Behavior

Ownership:
- `app.js` `drawChart()`, `updateTooltip()`, chart geometry, record labels
- Minimal `styles.css` chart sizing only if needed

Tasks:
- Consume `METRIC_META` from Lane A; do not introduce a parallel metric-label contract.
- Rename visible tooltip label from `session length` to `Active Time`.
- Update range-caption title/tooltip from first-token-to-last-token language to active-time gap language.
- Fix `rangePeakDay` to use selected metric/dayChartValue, not always `totalTokens`.
- Anchor `Most <metric>` record near the actual peak bar:
  - place above the peak bar when room exists
  - otherwise clamp inside chart bounds
  - keep it near the peak x-position when feasible
- Keep `Most active day` hidden when active time is toggled off.
- When active time is toggled off, reduce unneeded right/top padding and let bars use the reclaimed visual space.
- Preserve mobile chart behavior.

Lane verification:
- Browser inspect every metric with active time on/off.
- Confirm tooltip says `Active Time`.
- Confirm metric record follows the peak bar and stays inside chart.
- Confirm active-time line and active-time record disappear when toggled off.

### Lane C1: Hour Hover And Heatmap Flex

Ownership:
- `index.html` pattern markup
- `app.js` `updateHoursHistogram()` and hour hover handlers
- `styles.css` hour tooltip and heatmap layout

Tasks:
- Add an hour tooltip element, e.g. `#hoursTip`, inside the Hour Of Day pattern panel.
- Implement mouseover/mouseout/positioning for `.hour-bar`.
- Tooltip content should include:
  - hour label
  - formatted metric value
  - selected metric label
  - share of visible-range total
- Keep native `title` only as a fallback, not primary UX.
- Make heatmap flex to desktop panel width using shared CSS variables:
  - `--heatmap-cell`
  - `--heatmap-gap`
  - shared week-column sizing for `.heatmap-months` and `.heatmap-wrap`
- Preserve horizontal scrolling on narrow screens.
- Keep month labels aligned with heatmap columns.

Lane verification:
- Browser inspect hour hover behavior.
- Browser inspect heatmap at desktop width: cells expand to use available width.
- Browser/narrow inspect: heatmap remains usable and scrollable.

### Lane C2: Subagent Data Contract

Ownership:
- `tools/refresh_token_data.py`
- `tools/tests/test_refresh_token_data.py`
- `app.js` subagent card copy/denominator
- Methodology copy where relevant

Tasks:
- Add `SessionMeta.subagent: bool`.
- In `read_session_meta()`, set it from `source.subagent`.
- In `import_codex_usage()`, pass `subagent=meta.subagent` to Codex `UsageEvent`s after fork-bootstrap skip.
- Keep Claude `/subagents/` behavior.
- Do not count OpenCode as subagent until a reliable signal exists.
- Update `updateSubagentShare()` denominator to eligible providers only: Codex + Claude Code usage for visible range.
- Update caption to: `Subagent share of <metric label> across Codex + Claude Code.`
- Add or update fixtures proving Codex subagent aggregation:
  - a Codex subagent session contributes to `subagentUsage`
  - exported aggregate values do not include private process IDs, thread IDs, turn IDs, fork IDs, tool IDs, local paths, or message bodies
- Update methodology copy so it does not imply Claude-only subagent handling.

Lane verification:
- `uv run --quiet python -m unittest tools/tests/test_refresh_token_data.py`
- Browser inspect subagent card on real regenerated data.
- Confirm OpenCode is excluded from denominator unless a future signal is added.

## Coordinator Final Gate

Run after lane integration:

```bash
uv run --quiet python -m unittest tools/tests/test_refresh_token_data.py
node --check app.js
git diff --check
tools/update_dashboard.sh --no-commit --no-push
```

Browser verification:
- Desktop/live-like viewport:
  - default `Total`
  - metric switching across all four modes
  - smaller cost `(est.)`
  - plan pill stable and smaller
  - active-time copy correct
  - active-time toggle on/off
  - graph hover says `Active Time`
  - peak record follows bar
  - Hour Of Day hover
  - heatmap uses width
  - subagent card denominator/copy
- Narrow/mobile viewport:
  - graph still renders
  - controls wrap gracefully
  - heatmap remains usable

Commit after checks pass. Push/deploy only after explicit user approval or a follow-up instruction to ship.
