//@version=6
// ══════════════════════════════════════════════════════════════════════════
// LIQUIDITY POOLS PRO [WillyAlgoTrader]
// ══════════════════════════════════════════════════════════════════════════
// Author:  Willy | WillyAlgoTrader
// Version: 1.1.5

indicator(
     title              = "Liquidity Pools Pro [WillyAlgoTrader]",
     shorttitle         = "LP Pro",
     overlay            = true,
     max_lines_count    = 500,
     max_labels_count   = 500,
     max_boxes_count    = 500,
     max_bars_back      = 5000)

// ══════════════════════════════════════════════════════════════════════════
// 1. CONSTANTS
// ══════════════════════════════════════════════════════════════════════════
GRP_DETECT  = "🎯 Liquidity Detection"
GRP_SCORE   = "💪 Strength Scoring"
GRP_SIGNAL  = "📡 Sweep Signals"
GRP_HTF     = "🔭 HTF Confluence"
GRP_RISK    = "🛡️ Risk Management"
GRP_VISUAL  = "🎨 Visual"
GRP_DASH    = "📊 Dashboard"
GRP_ALERT   = "🔔 Alerts"
GRP_COLORS  = "🎨 Colors"

INDICATOR_VERSION = "v1.1.5"

// Zone states
int STATE_ACTIVE    = 0
int STATE_SWEPT     = 1
int STATE_MITIGATED = 2

// ══════════════════════════════════════════════════════════════════════════
// 2. INPUTS
// ══════════════════════════════════════════════════════════════════════════

// ── Detection ─────────────────────────────────────────────────────────────
adaptivePivotInput = input.bool(true, "Adaptive Pivot Length", group = GRP_DETECT,
     tooltip = "When ON: pivot length adapts to volatility (ATR-driven).\n• High volatility → longer pivots (less noise)\n• Low volatility → shorter pivots (more responsive)\nWhen OFF: uses Manual Pivot Length below.")

manualPivotLenInput = input.int(8, "Manual Pivot Length", minval = 2, maxval = 50, group = GRP_DETECT,
     tooltip = "Used when Adaptive Pivot is OFF. Bars on each side of a pivot.\n• 5–8: scalping/intraday\n• 10–15: swing\n• 20+: position trading")

rightConfirmInput = input.int(2, "Right Confirmation Bars", minval = 1, maxval = 10, group = GRP_DETECT,
     tooltip = "Bars to the right needed to confirm a pivot.\nLower = faster signals but more revisions.\nHigher = more reliable but lagging.")

atrToleranceInput = input.float(0.25, "Equality Tolerance (×ATR)", minval = 0.05, maxval = 2.0, step = 0.05, group = GRP_DETECT,
     tooltip = "Two pivots are 'equal' if their distance ≤ this × ATR.\n• 0.15: strict (tighter pools, fewer matches)\n• 0.25: balanced (recommended)\n• 0.50: loose (wider pools, more matches)\nWorks across instruments (BTC, EURUSD, indices) without re-tuning.")

maxLookbackInput = input.int(200, "Max Pivot Lookback (bars)", minval = 50, maxval = 1000, group = GRP_DETECT,
     tooltip = "Max bars to look back when matching a new pivot to historical pivots.\nHigher = catch older pools, slower compute.")

maxActivePoolsInput = input.int(40, "Max Active Pools", minval = 5, maxval = 100, group = GRP_DETECT,
     tooltip = "Maximum number of active (un-swept) liquidity pools tracked simultaneously.\nWeakest pools are pruned when exceeded.")

// ── Strength Scoring ──────────────────────────────────────────────────────
minStrengthShowInput = input.int(0, "Min Strength to Display", minval = 0, maxval = 100, group = GRP_SCORE,
     tooltip = "Pools with strength below this value are hidden.\n• 0: show all pools (recommended for visualization)\n• 30: filter out weak\n• 50+: only strong pools\nDoes NOT affect signal generation — see 'Min Pool Strength for Signal'.")

halfLifeBarsInput = input.int(150, "Strength Half-Life (bars)", minval = 20, maxval = 1000, group = GRP_SCORE,
     tooltip = "Number of bars after which a pool's age-strength halves.\nFresh pools score higher; old pools decay.\n• 50: aggressive decay\n• 150: balanced\n• 500: slow decay")

useVolumeWeightInput = input.bool(true, "Volume-Weighted Strength", group = GRP_SCORE,
     tooltip = "Add cumulative volume at level to strength score.\nDisable on instruments without reliable volume (some Forex).")

// ── Sweep Signals ─────────────────────────────────────────────────────────
enableSweepSignalsInput = input.bool(true, "Enable Sweep Signals (BUY/SELL)", group = GRP_SIGNAL,
     tooltip = "Generate BUY/SELL signals on liquidity sweep + reversal close.\nLogic: wick pierces level, then bar closes back inside (failed breakout).")

minStrengthSignalInput = input.int(25, "Min Pool Strength for Signal", minval = 0, maxval = 100, group = GRP_SIGNAL,
     tooltip = "Only generate sweep signals from pools with strength above this value.\nReference values:\n• Fresh 1-touch pool: ~35 strength\n• Fresh 2-touch pool: ~44\n• Fresh 3-touch pool: ~53\n• 25 (default): catches fresh single-touch pools\n• 40: requires 2+ touches\n• 60: only strong multi-touch + volume/HTF")

requireBodyReversalInput = input.bool(true, "Require Body Reversal", group = GRP_SIGNAL,
     tooltip = "ON: Bar close must be back inside (real reversal, recommended).\nOFF: Any wick sweep counts (more signals, more noise).")

// ── HTF Confluence ────────────────────────────────────────────────────────
useHtfConfluenceInput = input.bool(false, "Enable HTF Confluence", group = GRP_HTF,
     tooltip = "Boost strength of pools that align with higher-timeframe pivots.\nDisable for pure single-TF analysis.")

htfTimeframeInput = input.timeframe("240", "Higher Timeframe", group = GRP_HTF,
     tooltip = "Higher timeframe to check for pivot confluence (e.g., 240 = 4H, D = Daily).")

// ── Risk Management ───────────────────────────────────────────────────────
riskPresetInput = input.string("Balanced", "Risk Preset",
     options = ["Conservative", "Balanced", "Aggressive", "Scalping", "Custom"],
     group = GRP_RISK,
     tooltip = "Quick risk presets:\n• Conservative: SL 2.5×ATR, TP 1R/2R/4R\n• Balanced: SL 1.5×ATR, TP 1R/2R/3R\n• Aggressive: SL 1.0×ATR, TP 1.5R/2.5R/4R\n• Scalping: SL 0.8×ATR, TP 0.8R/1.5R/2R\n• Custom: use manual multipliers below")

atrLenRiskInput = input.int(14, "ATR Length (Risk)", minval = 5, maxval = 50, group = GRP_RISK,
     tooltip = "ATR period for stop-loss calculation.")

slMultInput  = input.float(1.5, "Custom SL ×ATR",  minval = 0.5, maxval = 5.0,  step = 0.1, group = GRP_RISK,
     tooltip = "Used when preset = Custom. SL distance as multiple of ATR.")
tp1MultInput = input.float(1.0, "Custom TP1 ×Risk", minval = 0.5, maxval = 5.0,  step = 0.1, group = GRP_RISK)
tp2MultInput = input.float(2.0, "Custom TP2 ×Risk", minval = 1.0, maxval = 10.0, step = 0.1, group = GRP_RISK)
tp3MultInput = input.float(3.0, "Custom TP3 ×Risk", minval = 1.5, maxval = 15.0, step = 0.1, group = GRP_RISK)

showSlTpInput       = input.bool(true,  "Show SL/TP Lines",  group = GRP_RISK)
showSlTpLabelsInput = input.bool(true,  "Show SL/TP Labels", group = GRP_RISK)

useBreakEvenInput = input.bool(true, "Break-Even After TP1", group = GRP_RISK,
     tooltip = "When TP1 is hit, move stop-loss to entry price (break-even).\nThe trailed SL replaces the original — trade closes at break-even\ninstead of original SL if price reverses.\nLets winners run to TP2/TP3 risk-free.")

showPctOnLabelsInput = input.bool(true, "Show % Distance on Labels", group = GRP_RISK,
     tooltip = "Append % distance from entry to SL/TP labels (e.g. 'SL 1.2300 (-0.36%)').\nHelps quick assessment of risk/reward without mental math.")

// ── Visual ────────────────────────────────────────────────────────────────
themeInput = input.string("Auto", "Theme",
     options = ["Auto", "Dark", "Light"],
     group = GRP_VISUAL,
     tooltip = "Chart color theme.\n• Auto: detect from chart background\n• Dark: optimized for dark backgrounds\n• Light: optimized for light backgrounds")

showZonesInput      = input.bool(true,  "Show Liquidity Zones",   group = GRP_VISUAL)
showStrengthBarInput= input.bool(true,  "Show Strength Bar",      group = GRP_VISUAL,
     tooltip = "Show colored intensity bar inside each zone reflecting strength score.")
showLabelsInput     = input.bool(true,  "Show Pool Labels",       group = GRP_VISUAL)
showVolumeInput     = input.bool(true,  "Show Volume in Labels",  group = GRP_VISUAL)
showSweptInput      = input.bool(true,  "Keep Swept Zones",       group = GRP_VISUAL,
     tooltip = "ON: dimmed swept zones remain on chart.\nOFF: zones are deleted on sweep.")
showSignalsInput    = input.bool(true,  "Show Buy/Sell Markers",  group = GRP_VISUAL)
showWatermarkInput  = input.bool(true,  "Show Watermark",         group = GRP_VISUAL)

labelFontSizeInput = input.string("Small", "SL/TP Label Font Size",
     options = ["Tiny", "Small", "Normal", "Large", "Huge"],
     group = GRP_VISUAL,
     tooltip = "Font size for SL/TP/Entry labels (price + percent values).\nDefault: Small. Use Tiny for compact look on busy charts.")

signalFontSizeInput = input.string("Small", "Signal Marker Size (Long/Short)",
     options = ["Tiny", "Small", "Normal", "Large", "Huge"],
     group = GRP_VISUAL,
     tooltip = "Font size for Long/Short signal markers below/above bars.\nDefault: Small. Use Normal/Large for better visibility on dense charts.")

// ── Dashboard ─────────────────────────────────────────────────────────────
showDashInput = input.bool(true, "Show Dashboard", group = GRP_DASH)
dashPosStr    = input.string("Top Right", "Position",
     options = ["Top Left", "Top Right", "Bottom Left", "Bottom Right", "Middle Right"],
     group = GRP_DASH)

// ── Alerts ────────────────────────────────────────────────────────────────
webhookInput = input.bool(false, "Webhook JSON Format", group = GRP_ALERT,
     tooltip = "Format alert messages as JSON for webhook integrations (autotrading bots).")

alertSlHitInput = input.bool(true,  "Alert on SL Hit",        group = GRP_ALERT,
     tooltip = "Send alert when stop-loss is hit.")
alertTpHitInput = input.bool(false, "Alert on TP Hits & BE",  group = GRP_ALERT,
     tooltip = "Send alerts for TP1/TP2/TP3 hits and break-even activation.\nDisabled by default (can be noisy).")

// ── Colors ────────────────────────────────────────────────────────────────
bullColorInput = input.color(#00E676, "Bullish (Demand) Color", group = GRP_COLORS)
bearColorInput = input.color(#FF5252, "Bearish (Supply) Color", group = GRP_COLORS)

// ══════════════════════════════════════════════════════════════════════════
// 3. THEME DETECTION & PALETTE
// ══════════════════════════════════════════════════════════════════════════
isDark = switch themeInput
    "Dark"  => true
    "Light" => false
    =>         color.r(chart.bg_color) < 128

// Base text
TEXT_COLOR = isDark ? #E0E0E0 : #1A1A1A
TEXT_MUTED = isDark ? color.new(#9E9E9E, 0) : color.new(#757575, 0)

// Adaptive signal text (on chart bg)
// Light variants are darkened from raw bull/bear/neutral values to ensure
// WCAG AA contrast (4.5:1+) on white chart backgrounds.
BULL_TEXT     = isDark ? #00E676 : #00695C   // deep teal-green on light (was #00A152, 3.38:1 marginal)
BEAR_TEXT     = isDark ? #FF5252 : #D32F2F   // 4.98:1 on white — passes AA
NEUTRAL_TEXT  = isDark ? #FFEB3B : #BF360C   // dark deep-orange on light bg (was #F57F17, 2.65:1 fail)

// Label text on signal backgrounds
BULL_LABEL_TEXT = #004D25  // dark green on bright green (contrast 7.8:1)
BEAR_LABEL_TEXT = #FFFFFF  // white on red

// Tables
TABLE_BG      = isDark ? color.new(#131722, 5)  : color.new(#FFFFFF, 5)
TABLE_BORDER  = isDark ? color.new(#2A2E39, 50) : color.new(#D0D0D0, 50)
TABLE_ROW_ALT = isDark ? color.new(#1C2030, 0)  : color.new(#F0F4F8, 0)
HEADER_BG     = color.new(#1565C0, 0)
HEADER_TEXT   = #FFFFFF

// SL / TP / Entry — Pro palette (muted-saturated, AFT-style)
// Pro style favors muted-saturated tones over neon brights.
// Lines stay readable but don't scream — emphasis comes from positioning.
// v1.1.4: TP switched from turquoise (#26A69A/#00796B) to Material green
// for the more universal "profit = green" semantic. Adaptive contrast preserved.
SL_COLOR    = isDark ? color.new(#EF5350, 0)  : color.new(#C62828, 0)
TP1_COLOR   = isDark ? color.new(#4CAF50, 25) : color.new(#2E7D32, 25)
TP2_COLOR   = isDark ? color.new(#4CAF50, 35) : color.new(#2E7D32, 35)
TP3_COLOR   = isDark ? color.new(#4CAF50, 0)  : color.new(#2E7D32, 0)   // TP3 full opacity for emphasis
ENTRY_COLOR = isDark ? color.new(#42A5F5, 0)  : color.new(#1565C0, 0)   // blue (already adaptive — kept as is)

// Pro accent + utility colors
TP_HIT_COLOR = isDark ? color.new(#26C6DA, 0) : color.new(#00838F, 0)   // soft cyan for hit TP
BE_COLOR     = isDark ? color.new(#FFA726, 0) : color.new(#BF360C, 0)   // amber/burnt-orange for break-even (light tuned for 5.60:1 on white)
// LABEL_TEXT — text color on tinted SL/TP/Entry/BE/TP_HIT label backgrounds.
// Adaptive: dark theme uses muted-saturated bg colors (lighter tones) → needs DARK text;
// light theme uses darker variants of same colors → white text works fine.
// Without this adaptation, dark-theme labels had contrast ratios as low as 1.94:1
// (BE amber + white) which fails WCAG AA.
LABEL_TEXT   = isDark ? #1A1A1A : #FFFFFF

// Watermark
WM_COLOR = isDark ? color.new(#FFFFFF, 80) : color.new(#000000, 80)

// Zone colors derived from inputs
BULL_ZONE_BORDER = bullColorInput
BEAR_ZONE_BORDER = bearColorInput
SWEPT_BORDER     = isDark ? color.new(#9E9E9E, 60) : color.new(#616161, 60)
SWEPT_BG         = isDark ? color.new(#9E9E9E, 92) : color.new(#9E9E9E, 88)
MITIGATED_BORDER = isDark ? color.new(#616161, 70) : color.new(#424242, 70)
MITIGATED_BG     = isDark ? color.new(#616161, 94) : color.new(#424242, 92)

// Label background (transparent so only text floats)
LABEL_BG_TRANSPARENT = color.new(color.black, 100)

// ══════════════════════════════════════════════════════════════════════════
// 4. RISK PRESET RESOLUTION
// ══════════════════════════════════════════════════════════════════════════
[effectiveSLMult, effectiveTP1Mult, effectiveTP2Mult, effectiveTP3Mult] = switch riskPresetInput
    "Conservative" => [2.5, 1.0, 2.0, 4.0]
    "Aggressive"   => [1.0, 1.5, 2.5, 4.0]
    "Scalping"     => [0.8, 0.8, 1.5, 2.0]
    "Custom"       => [slMultInput, tp1MultInput, tp2MultInput, tp3MultInput]
    =>                [1.5, 1.0, 2.0, 3.0]   // Balanced

// ══════════════════════════════════════════════════════════════════════════
// 5. DASHBOARD POSITION
// ══════════════════════════════════════════════════════════════════════════
dashPos = switch dashPosStr
    "Top Left"     => position.top_left
    "Top Right"    => position.top_right
    "Bottom Left"  => position.bottom_left
    "Bottom Right" => position.bottom_right
    "Middle Right" => position.middle_right
    =>                position.top_right

// ══════════════════════════════════════════════════════════════════════════
// 6. UTILITY FUNCTIONS
// ══════════════════════════════════════════════════════════════════════════
safeDiv(float num, float den, float fallback = 0.0) =>
    den != 0 and not na(num) and not na(den) ? num / den : fallback

// Format percent distance from entry, with sign. Returns "" if entry/level invalid
// or showPctOnLabelsInput is OFF. Used to suffix SL/TP labels.
formatPctFromEntry(float level, float entry) =>
    string res = ""
    if showPctOnLabelsInput and not na(level) and not na(entry) and entry != 0
        float pct = (level - entry) / entry * 100.0
        string sign = pct >= 0 ? "+" : ""
        res := " (" + sign + str.tostring(pct, "#.##") + "%)"
    res

// Resolve user font size string to size constant
sizeFromString(string s) =>
    switch s
        "Tiny"   => size.tiny
        "Small"  => size.small
        "Normal" => size.normal
        "Large"  => size.large
        "Huge"   => size.huge
        =>          size.small

formatVolume(float v) =>
    string res = "—"
    if not na(v)
        if v >= 1e9
            res := str.format("{0,number,#.##}B", v / 1e9)
        else if v >= 1e6
            res := str.format("{0,number,#.##}M", v / 1e6)
        else if v >= 1e3
            res := str.format("{0,number,#.#}K", v / 1e3)
        else
            res := str.tostring(v, "#")
    res

// Strength → color intensity. Strong = saturated; weak = transparent.
strengthToTransparency(float strength) =>
    // strength ∈ [0, 100]; map to transparency [88, 55]
    int t = int(88.0 - (math.max(0.0, math.min(100.0, strength)) * 0.33))
    t

// ══════════════════════════════════════════════════════════════════════════
// 7. ADAPTIVE PIVOT LENGTH
// ══════════════════════════════════════════════════════════════════════════
float atrFast = nz(ta.atr(14), 0.0)
float atrSlow = nz(ta.atr(50), 0.0)

// Volatility ratio: > 1 = current more volatile than avg → shorter pivot
float volRatio = safeDiv(atrFast, atrSlow, 1.0)

// Adaptive left length: 4 (high vol) ... 16 (low vol)
// Note: dynamic series-int length is supported by ta.pivothigh in v6,
// but we ensure max_bars_back covers the full possible range.
int adaptiveLeftLen = adaptivePivotInput ? int(math.round(math.max(4.0, math.min(16.0, 10.0 / math.max(0.5, volRatio))))) : manualPivotLenInput

int leftLen  = adaptiveLeftLen
int rightLen = rightConfirmInput

// Warmup
int WARMUP_BARS = math.max(50, atrLenRiskInput + 10)
bool isWarmedUp = bar_index >= WARMUP_BARS

// ══════════════════════════════════════════════════════════════════════════
// 8. TYPES
// ══════════════════════════════════════════════════════════════════════════
type LiquidityPool
    float    level         // representative price (avg of all touches)
    float    levelTop      // upper bound (level + atr*tolerance/2)
    float    levelBot      // lower bound
    int      lastTouchIdx   // bar index of most recent touch (for sweep cooldown + age)
    int      firstBarTime   // time of pool creation (for box/line drawing — avoids bar-distance limits)
    int      touchCount
    float    cumulativeVol  // sum of volume of bars whose high/low touched within tolerance
    bool     isHigh        // sell-side liquidity (highs) vs buy-side (lows)
    int      state         // STATE_ACTIVE / STATE_SWEPT / STATE_MITIGATED
    int      sweptAtIdx     // bar index of sweep (for prune ordering)
    int      sweptAtTime    // time of sweep (for box right edge)
    float    htfBonus      // 0 or +20 if HTF confluence
    float    strength      // 0–100 (cached, recomputed lazily)
    box      zoneBox
    label    poolLabel
    line     midLine

// ══════════════════════════════════════════════════════════════════════════
// 9. STORAGE
// ══════════════════════════════════════════════════════════════════════════
var array<LiquidityPool> pools = array.new<LiquidityPool>()

// Diagnostics counters (for Dashboard debugging — why no signals?)
var int totalMitigationsCount = 0  // any mitigation event regardless of strength
var int totalSweepsCount      = 0  // wick-only sweeps (didn't close back)
// signalsFiredCount counts GENERATED signals (passed strength + body filter), NOT executed
// trades. Position lock may block some from opening — but the count still increments.
// To debug "why no entry": compare with dashboard "Position" row.
var int signalsFiredCount     = 0
var int totalPoolsEverCount   = 0  // pools created since chart start

// ══════════════════════════════════════════════════════════════════════════
// 10. PIVOT DETECTION
// ══════════════════════════════════════════════════════════════════════════
float ph = ta.pivothigh(leftLen, rightLen)
float pl = ta.pivotlow(leftLen, rightLen)

// Equality tolerance — reuse atrFast (declared in section 7) instead of
// computing ta.atr(14) again. Same value, half the cost.
float tolerance = atrFast * atrToleranceInput

// HTF pivot levels (non-repainting)
// IMPORTANT: in Pine v6, short-circuit eval means functions inside ternaries
// may not run consistently. We always call request.security() and apply the
// useHtfConfluenceInput flag separately below.
float _htfPH = request.security(syminfo.tickerid, htfTimeframeInput, ta.pivothigh(3, 3)[1], barmerge.gaps_off, barmerge.lookahead_on)
float _htfPL = request.security(syminfo.tickerid, htfTimeframeInput, ta.pivotlow(3, 3)[1],  barmerge.gaps_off, barmerge.lookahead_on)
float htfPivotHigh = useHtfConfluenceInput ? _htfPH : na
float htfPivotLow  = useHtfConfluenceInput ? _htfPL : na

// ══════════════════════════════════════════════════════════════════════════
// 11. STRENGTH SCORING
// ══════════════════════════════════════════════════════════════════════════
// Score components (each 0–N pts, weighted), total clamped to 0–100:
//   • Touches:    (touchCount-1)*9, capped at 35 → 1=0, 2=9, 3=18, 4=27, 5+=35
//   • Recency:    age decay via half-life → up to 35 pts (fresh = 35)
//   • Volume:     cumVol vs median → up to 20 pts
//   • HTF:        bonus +20 if HTF confluent
//
// Examples:
//   Fresh 1-touch pool, no volume, no HTF:        0 + 35 +  0 +  0 = 35
//   Fresh 2-touch pool, no volume, no HTF:        9 + 35 +  0 +  0 = 44
//   Fresh 3-touch pool, 2x median volume, no HTF: 18 + 35 + 5.5 + 0 = 58.5
//   Old (1 half-life) 2-touch pool, no extras:    9 + 17.5 + 0 + 0 = 26.5

var float volMedian = na

method computeStrength(LiquidityPool p) =>
    int   ageBars    = bar_index - p.lastTouchIdx
    float decay      = math.pow(0.5, safeDiv(ageBars, halfLifeBarsInput, 0.0))
    // touchCount ≥ 1 always; (count-1)*9 capped at 35: 1→0, 2→9, 3→18, 4→27, 5+→35
    float touchPts   = math.min(35.0, (p.touchCount - 1) * 9.0)
    float recencyPts = 35.0 * decay
    float volPts     = 0.0
    if useVolumeWeightInput and not na(volMedian) and volMedian > 0
        float r = safeDiv(p.cumulativeVol, volMedian, 0.0)
        // log scaling: r=1 → 0 pts, r=2 → ~5.5 pts, r=10 → ~18.4 pts, r→∞ capped at 20
        volPts := math.min(20.0, math.log(math.max(1.0, r)) * 8.0)
    float total = touchPts + recencyPts + volPts + p.htfBonus
    p.strength := math.max(0.0, math.min(100.0, total))
    p.strength

// ══════════════════════════════════════════════════════════════════════════
// 12. POOL MATCHING & CREATION
// ══════════════════════════════════════════════════════════════════════════
// Try to match a new pivot to an existing pool. Returns pool index or -1.
findMatchingPool(float price, bool isHigh, float tol) =>
    int foundIdx = -1
    int sz = pools.size()
    if sz > 0
        for i = 0 to sz - 1
            LiquidityPool p = pools.get(i)
            if p.state == STATE_ACTIVE and p.isHigh == isHigh
                float dist = math.abs(price - p.level)
                if dist <= tol and (bar_index - p.lastTouchIdx) <= maxLookbackInput
                    foundIdx := i
                    break
    foundIdx

// Update existing pool with new touch. Returns the new touch count.
// Note: in Pine v6, UDT fields are reference-mutated; pools.set() is not needed.
updatePoolWithTouch(int poolIdx, float touchPrice, int touchIdx, float touchVol) =>
    LiquidityPool p = pools.get(poolIdx)
    // Recalculate level as weighted average (older touches retain memory)
    float newLevel = (p.level * p.touchCount + touchPrice) / (p.touchCount + 1)
    p.level         := newLevel
    p.levelTop      := newLevel + tolerance / 2.0
    p.levelBot      := newLevel - tolerance / 2.0
    p.lastTouchIdx  := touchIdx
    p.touchCount    := p.touchCount + 1
    p.cumulativeVol := p.cumulativeVol + touchVol
    p.touchCount   // return for Pine Script v6 (function must return a value)

// Create a new pool from a pivot (or single pivot if no match). Returns the new pool size.
// Uses xloc.bar_time for box/line so they remain valid when bar distance grows beyond max_bars_back.
// NOTE: cannot modify global counters from inside a function in Pine v6 — caller increments them.
createPool(float price, int idx, int t, float vol, bool isHigh, float htfBonus) =>
    color borderClr = isHigh ? BEAR_ZONE_BORDER : BULL_ZONE_BORDER
    color bgClr     = color.new(borderClr, 88)
    box  newBox  = box.new(t, price + tolerance / 2.0, t, price - tolerance / 2.0, border_color = borderClr, bgcolor = bgClr, border_width = 1, xloc = xloc.bar_time)
    line newLine = line.new(t, price, t, price, color = color.new(borderClr, 50), style = line.style_dotted, width = 1, xloc = xloc.bar_time)
    label newLbl = label.new(t, price, "", color = LABEL_BG_TRANSPARENT, textcolor = isHigh ? BEAR_TEXT : BULL_TEXT, style = label.style_label_left, size = size.small, xloc = xloc.bar_time)
    LiquidityPool p = LiquidityPool.new(price, price + tolerance / 2.0, price - tolerance / 2.0, idx, t, 1, vol, isHigh, STATE_ACTIVE, 0, 0, htfBonus, 0.0, newBox, newLbl, newLine)
    pools.push(p)
    pools.size()   // return value for Pine Script v6

// ══════════════════════════════════════════════════════════════════════════
// 13. PROCESS NEW PIVOTS
// ══════════════════════════════════════════════════════════════════════════
if not na(ph) and isWarmedUp
    int  pivotIdx  = bar_index - rightLen
    int  pivotTime = time[rightLen]
    float pVol     = nz(volume[rightLen], 0.0)
    // HTF confluence
    float htfBonus = useHtfConfluenceInput and not na(htfPivotHigh) and math.abs(ph - htfPivotHigh) <= tolerance * 1.5 ? 20.0 : 0.0
    int matchIdx = findMatchingPool(ph, true, tolerance)
    if matchIdx >= 0
        updatePoolWithTouch(matchIdx, ph, pivotIdx, pVol)
        LiquidityPool pp = pools.get(matchIdx)
        // HTF bonus accumulates if confluent again (UDT field assignment is by reference)
        if htfBonus > 0 and pp.htfBonus < 20.0
            pp.htfBonus := 20.0
    else
        createPool(ph, pivotIdx, pivotTime, pVol, true, htfBonus)
        totalPoolsEverCount := totalPoolsEverCount + 1

if not na(pl) and isWarmedUp
    int  pivotIdx  = bar_index - rightLen
    int  pivotTime = time[rightLen]
    float pVol     = nz(volume[rightLen], 0.0)
    float htfBonus = useHtfConfluenceInput and not na(htfPivotLow) and math.abs(pl - htfPivotLow) <= tolerance * 1.5 ? 20.0 : 0.0
    int matchIdx = findMatchingPool(pl, false, tolerance)
    if matchIdx >= 0
        updatePoolWithTouch(matchIdx, pl, pivotIdx, pVol)
        LiquidityPool pp = pools.get(matchIdx)
        if htfBonus > 0 and pp.htfBonus < 20.0
            pp.htfBonus := 20.0
    else
        createPool(pl, pivotIdx, pivotTime, pVol, false, htfBonus)
        totalPoolsEverCount := totalPoolsEverCount + 1

// Volume median for scoring — recomputed every 25 bars (cheap, ~40 pools)
// Initialize as soon as we have enough pools, then refresh periodically.
if pools.size() >= 3 and (na(volMedian) or bar_index % 25 == 0)
    array<float> vols = array.new<float>()
    for i = 0 to pools.size() - 1
        vols.push(pools.get(i).cumulativeVol)
    if vols.size() > 0
        volMedian := array.median(vols)

// ══════════════════════════════════════════════════════════════════════════
// 14. VOLUME-AT-LEVEL ACCUMULATION
// ══════════════════════════════════════════════════════════════════════════
// On every bar, if current bar's high or low touches an active pool's zone,
// add bar's volume to that pool's cumulative volume. This is a more accurate
// "liquidity at level" measure than just summing pivot-bar volumes.
// O(P) per bar where P = pool count (~40), negligible.
if pools.size() > 0 and isWarmedUp and barstate.isconfirmed and useVolumeWeightInput
    float barVol = nz(volume, 0.0)
    if barVol > 0
        for i = 0 to pools.size() - 1
            LiquidityPool p = pools.get(i)
            if p.state == STATE_ACTIVE
                bool touched = (high >= p.levelBot and high <= p.levelTop) or (low >= p.levelBot and low <= p.levelTop) or (low <= p.levelBot and high >= p.levelTop)
                if touched and bar_index > p.lastTouchIdx
                    // Don't double-count the pivot bar itself; only count other bars
                    p.cumulativeVol := p.cumulativeVol + barVol

// ══════════════════════════════════════════════════════════════════════════
// 15. SWEEP & MITIGATION DETECTION + SIGNAL GENERATION
// ══════════════════════════════════════════════════════════════════════════
// Pool state transitions:
//   STATE_ACTIVE → STATE_SWEPT     : wick pierced level but bar didn't close back
//                                    (level was broken — likely BoS / continuation)
//   STATE_ACTIVE → STATE_MITIGATED : wick pierced AND bar closed back inside
//                                    (real liquidity grab — failed breakout, reversal)
//
// Signal logic:
//   • Default (requireBodyReversalInput = ON): only fire on MITIGATED (proper SMC setup).
//   • Aggressive (requireBodyReversalInput = OFF): fire on any wick sweep — more signals,
//     more noise, but catches early reversals before the candle closes back.
//
// Notes:
// - Cooldown: skip pivot's own confirmation bars + 1 (so pool "settles" before sweep eligible).
// - signalsFiredCount is incremented only ONCE per bar (even if multiple pools fire).

bool sweepBuySignal  = false  // sweep of LOW + close above → BUY
bool sweepSellSignal = false  // sweep of HIGH + close below → SELL
float signalPoolStrength = 0.0
bool anySignalFiredThisBar = false  // dedup counter increment

if pools.size() > 0 and isWarmedUp and barstate.isconfirmed
    for i = 0 to pools.size() - 1
        LiquidityPool p = pools.get(i)
        if p.state == STATE_ACTIVE and bar_index >= p.lastTouchIdx + rightLen + 1
            float s = p.computeStrength()

            // Direction-agnostic wick/close logic
            bool wickSweep = p.isHigh ? high > p.levelTop : low < p.levelBot
            bool closeBack = p.isHigh ? close < p.level   : close > p.level

            if wickSweep
                // Classification: MITIGATED only if real reversal (close back inside).
                // Wick-only sweep without close-back means the level was broken (SWEPT).
                if closeBack
                    p.state       := STATE_MITIGATED
                    p.sweptAtIdx  := bar_index
                    p.sweptAtTime := time
                    totalMitigationsCount := totalMitigationsCount + 1
                else
                    p.state       := STATE_SWEPT
                    p.sweptAtIdx  := bar_index
                    p.sweptAtTime := time
                    totalSweepsCount := totalSweepsCount + 1

                // Signal: by default only on mitigation; if user disables body-reversal
                // requirement, fire on any wick sweep (caller takes responsibility).
                bool signalEligible = requireBodyReversalInput ? closeBack : true
                if enableSweepSignalsInput and signalEligible and s >= minStrengthSignalInput
                    if p.isHigh
                        sweepSellSignal := true
                    else
                        sweepBuySignal := true
                    if s > signalPoolStrength
                        signalPoolStrength := s
                    anySignalFiredThisBar := true

if anySignalFiredThisBar
    signalsFiredCount := signalsFiredCount + 1

// ══════════════════════════════════════════════════════════════════════════
// 16. PRUNE WEAK / EXCESS POOLS
// ══════════════════════════════════════════════════════════════════════════
// Strategy:
//   1. If "Keep Swept Zones" is OFF, immediately remove all non-active pools.
//   2. If size > maxActivePools, drop oldest swept first; then weakest active.
// Note: Pine doesn't allow reverse for-loops, and removing during forward
// iteration corrupts indices. Pattern: collect indices to delete, then remove
// from highest to lowest index.
if pools.size() > 0 and barstate.isconfirmed
    // Step 1: prune swept/mitigated pools if user opted out
    if not showSweptInput
        array<int> toRemove = array.new<int>()
        for i = 0 to pools.size() - 1
            LiquidityPool p = pools.get(i)
            if p.state != STATE_ACTIVE
                toRemove.push(i)
        // Remove highest-to-lowest so earlier indices stay valid
        if toRemove.size() > 0
            for k = 0 to toRemove.size() - 1
                int idx = toRemove.get(toRemove.size() - 1 - k)
                LiquidityPool p = pools.get(idx)
                box.delete(p.zoneBox)
                line.delete(p.midLine)
                label.delete(p.poolLabel)
                pools.remove(idx)

    // Step 2: enforce limit (single-pool drop per bar — sufficient since pivots are sparse)
    if pools.size() > maxActivePoolsInput
        // Try to drop oldest swept first
        int oldestSweptIdx = -1
        int oldestSweptIdxBar = bar_index + 1
        for i = 0 to pools.size() - 1
            LiquidityPool p = pools.get(i)
            if p.state != STATE_ACTIVE and p.sweptAtIdx < oldestSweptIdxBar
                oldestSweptIdxBar := p.sweptAtIdx
                oldestSweptIdx := i
        if oldestSweptIdx >= 0
            LiquidityPool victim = pools.get(oldestSweptIdx)
            box.delete(victim.zoneBox)
            line.delete(victim.midLine)
            label.delete(victim.poolLabel)
            pools.remove(oldestSweptIdx)
        else
            // No swept pools — drop weakest active
            int worstIdx = -1
            float worstStr = 1e9
            for i = 0 to pools.size() - 1
                LiquidityPool p = pools.get(i)
                if p.state == STATE_ACTIVE
                    float s = p.computeStrength()
                    if s < worstStr
                        worstStr := s
                        worstIdx := i
            if worstIdx >= 0
                LiquidityPool victim = pools.get(worstIdx)
                box.delete(victim.zoneBox)
                line.delete(victim.midLine)
                label.delete(victim.poolLabel)
                pools.remove(worstIdx)

// ══════════════════════════════════════════════════════════════════════════
// 17. RENDER POOLS (only on last bar — performance)
// ══════════════════════════════════════════════════════════════════════════
// Pools use xloc.bar_time for box/line/label drawables, so set_left/right/x
// accept TIME values (UNIX timestamps), not bar_index. This avoids the
// "bar index too far from current bar" error on long histories.
if barstate.islast and pools.size() > 0
    for i = 0 to pools.size() - 1
        LiquidityPool p = pools.get(i)
        float s = p.computeStrength()
        bool visible = showZonesInput and (p.state == STATE_ACTIVE ? s >= minStrengthShowInput : showSweptInput)

        // Always update geometry to current bar
        if visible
            int rightT = p.state == STATE_ACTIVE ? time : p.sweptAtTime
            box.set_left  (p.zoneBox, p.firstBarTime)
            box.set_right (p.zoneBox, rightT)
            box.set_top   (p.zoneBox, p.levelTop)
            box.set_bottom(p.zoneBox, p.levelBot)
            line.set_x1(p.midLine, p.firstBarTime)
            line.set_x2(p.midLine, rightT)
            line.set_y1(p.midLine, p.level)
            line.set_y2(p.midLine, p.level)

            color baseClr = p.isHigh ? BEAR_ZONE_BORDER : BULL_ZONE_BORDER
            int   bgTransp = showStrengthBarInput ? strengthToTransparency(s) : 88
            color borderClr = baseClr
            color bgClr     = color.new(baseClr, bgTransp)

            if p.state == STATE_SWEPT
                borderClr := SWEPT_BORDER
                bgClr     := SWEPT_BG
            else if p.state == STATE_MITIGATED
                borderClr := MITIGATED_BORDER
                bgClr     := MITIGATED_BG

            box.set_border_color(p.zoneBox, borderClr)
            box.set_bgcolor     (p.zoneBox, bgClr)
            line.set_color(p.midLine, color.new(borderClr, 40))

            if showLabelsInput
                string typeStr = p.isHigh ? "SSL" : "BSL"   // sell-side / buy-side liquidity
                string countStr = p.touchCount > 1 ? str.format(" ×{0}", p.touchCount) : ""
                string strStr   = str.format(" [{0}]", math.round(s))
                string volStr   = showVolumeInput ? " " + formatVolume(p.cumulativeVol) : ""
                string stateStr = p.state == STATE_SWEPT ? "Swept " : p.state == STATE_MITIGATED ? "Mit. " : ""
                label.set_x   (p.poolLabel, rightT)
                label.set_y   (p.poolLabel, p.level)
                label.set_text(p.poolLabel, stateStr + typeStr + countStr + strStr + volStr)
                label.set_textcolor(p.poolLabel, p.state == STATE_ACTIVE ? (p.isHigh ? BEAR_TEXT : BULL_TEXT) : color.new(TEXT_MUTED, 20))
            else
                label.set_text(p.poolLabel, "")
        else
            // Hide via collapsing geometry (use current bar time)
            box.set_left (p.zoneBox, time)
            box.set_right(p.zoneBox, time)
            line.set_x1(p.midLine, time)
            line.set_x2(p.midLine, time)
            label.set_text(p.poolLabel, "")

// ══════════════════════════════════════════════════════════════════════════
// 18. SIGNAL VISUAL MARKERS
// ══════════════════════════════════════════════════════════════════════════
// Using label.new (not plotshape) so the marker size can be dynamic
// (plotshape only accepts simple/const size values, label.new accepts series).
//
// Markers fire on the SAME bar as the signal — the trade opens at this bar's
// close, SL/TP lines appear immediately, and BUY/SELL alert is sent.
signalMarkerSize = sizeFromString(signalFontSizeInput)

if showSignalsInput and sweepBuySignal
    label.new(bar_index, low, "Long",
         color     = bullColorInput,
         textcolor = BULL_LABEL_TEXT,
         style     = label.style_label_up,
         size      = signalMarkerSize,
         yloc      = yloc.belowbar)

if showSignalsInput and sweepSellSignal
    label.new(bar_index, high, "Short",
         color     = bearColorInput,
         textcolor = BEAR_LABEL_TEXT,
         style     = label.style_label_down,
         size      = signalMarkerSize,
         yloc      = yloc.abovebar)

// ══════════════════════════════════════════════════════════════════════════
// 19. RISK MANAGEMENT — SL/TP CALCULATION & STATE
// ══════════════════════════════════════════════════════════════════════════
float riskAtr    = nz(ta.atr(atrLenRiskInput), 0.0)
float slDistance = riskAtr * effectiveSLMult

var float activeEntry = na
var float activeSL    = na
var float activeTP1   = na
var float activeTP2   = na
var float activeTP3   = na
var int   activeDir   = 0     // 1 long, -1 short, 0 flat
var float activePoolStrength = na

// Latched flags — fire each TP only once per trade, prevent duplicate alerts
var bool tp1Reached = false
var bool tp2Reached = false
var bool tp3Reached = false

// Break-even tracking — set when SL is moved to entry after TP1
var bool beActive = false

// Direction-agnostic resolved signals
// Position lock: while a trade is active, new signals are blocked.
// If we don't lock, a fresh signal in the opposite direction would silently
// overwrite the open position's SL/TP — bad for live trading.
//
// Special case: if the current bar would close the trade (SL or TP3 hit),
// we treat the position as "closing now" and allow signals through.
// The auto-reset block below clears state on this same bar, so the new
// signal can proceed cleanly.
bool barWouldCloseLong  = activeDir == 1  and not na(activeSL) and not na(activeTP3) and (low  <= activeSL or high >= activeTP3)
bool barWouldCloseShort = activeDir == -1 and not na(activeSL) and not na(activeTP3) and (high >= activeSL or low  <= activeTP3)
bool positionActive = activeDir != 0 and not barWouldCloseLong and not barWouldCloseShort

confirmedBuy  = sweepBuySignal  and barstate.isconfirmed and isWarmedUp and not positionActive
confirmedSell = sweepSellSignal and barstate.isconfirmed and isWarmedUp and not positionActive

// Track whether the trade was opened on the current bar.
// Prevents same-bar SL/TP detection on a freshly opened trade
// (would otherwise misfire if the bar's wick already crossed new SL/TP levels).
bool justOpened = false

if (confirmedBuy or confirmedSell)
    int  newDir = confirmedBuy ? 1 : -1
    float sign  = confirmedBuy ? 1.0 : -1.0
    activeEntry := close
    activeSL    := close - sign * slDistance
    activeTP1   := close + sign * slDistance * effectiveTP1Mult
    activeTP2   := close + sign * slDistance * effectiveTP2Mult
    activeTP3   := close + sign * slDistance * effectiveTP3Mult
    activeDir   := newDir
    activePoolStrength := signalPoolStrength
    tp1Reached  := false
    tp2Reached  := false
    tp3Reached  := false
    beActive    := false
    justOpened  := true

// Hit detection — single helper for all levels.
// Skipped on the same bar a trade opens (justOpened) — see comment above.
//
// Break-even handling: SL hit is checked against the SL value AT THE START
// of this bar (effectiveSL), not against any new BE level applied later in
// the same bar. This ensures TP1 + SL on the same bar resolves correctly:
// TP1 fires from the bar's wick, then SL moves to entry — but stop-out of
// THIS bar still uses original SL (not the BE level).
float effectiveSL = activeSL  // captured before any BE update on this bar

bool slHit  = not justOpened and (activeDir == 1  ? low  <= effectiveSL : activeDir == -1 ? high >= effectiveSL : false)
bool tp1Hit = not justOpened and (activeDir == 1  ? high >= activeTP1   : activeDir == -1 ? low  <= activeTP1   : false)
bool tp2Hit = not justOpened and (activeDir == 1  ? high >= activeTP2   : activeDir == -1 ? low  <= activeTP2   : false)
bool tp3Hit = not justOpened and (activeDir == 1  ? high >= activeTP3   : activeDir == -1 ? low  <= activeTP3   : false)

// Latch TP flags — one-shot per trade
bool tp1FirstTouch = tp1Hit and not tp1Reached and activeDir != 0
bool tp2FirstTouch = tp2Hit and not tp2Reached and activeDir != 0
bool tp3FirstTouch = tp3Hit and not tp3Reached and activeDir != 0

if tp1FirstTouch
    tp1Reached := true
if tp2FirstTouch
    tp2Reached := true
if tp3FirstTouch
    tp3Reached := true

// ── Break-even trail ──
// On TP1 first touch (without simultaneous SL hit), move SL to entry.
// From the next bar onward, any wick touching entry = stop-out at break-even.
bool beJustActivated = false
if useBreakEvenInput and tp1FirstTouch and not slHit and activeDir != 0 and not beActive
    activeSL := activeEntry
    beActive := true
    beJustActivated := true

// Auto-reset on SL hit or TP3 hit
if (slHit or tp3Hit) and activeDir != 0
    activeDir          := 0
    activeSL           := na
    activeTP1          := na
    activeTP2          := na
    activeTP3          := na
    activeEntry        := na
    activePoolStrength := na
    tp1Reached         := false
    tp2Reached         := false
    tp3Reached         := false
    beActive           := false

// ══════════════════════════════════════════════════════════════════════════
// 20. SL/TP LINES & LABELS — AFT-style Pro design
// ══════════════════════════════════════════════════════════════════════════
// Style guide (inspired by AFT v1.5.4):
//   • Entry: dotted blue, width 1
//   • SL:    solid red, width 1 (dimmed when BE is active)
//   • TP1:   dashed green, width 1 (→ solid cyan + ✓ on hit)
//   • TP2:   dashed green, width 1 (→ solid cyan + ✓ on hit)
//   • TP3:   dashed green, width 1 (→ solid cyan + ✓ on hit)
//   • Labels: tinted backgrounds matching line color + adaptive text
//   • % distance from entry shown on each label (toggleable)

// Resolve label size from input
labelSize = sizeFromString(labelFontSizeInput)

// Layout constants
int LINE_LOOKAHEAD = 30   // initial line projection in bars
int LINE_EXTEND    = 5    // continuous extension while active
int LABEL_OFFSET   = 31   // label x-offset from current bar

var line  slLine    = na
var line  tp1Line   = na
var line  tp2Line   = na
var line  tp3Line   = na
var line  entryLine = na
var label slLabel    = na
var label tp1Label   = na
var label tp2Label   = na
var label tp3Label   = na
var label entryLabel = na
var bool  riskLinesActive = false

if (confirmedBuy or confirmedSell) and showSlTpInput
    line.delete(slLine),     line.delete(tp1Line),  line.delete(tp2Line)
    line.delete(tp3Line),    line.delete(entryLine)
    label.delete(slLabel),   label.delete(tp1Label), label.delete(tp2Label)
    label.delete(tp3Label),  label.delete(entryLabel)

    int startBar = bar_index

    // Pro-style line hierarchy: entry dotted, SL solid (1px), TPs dashed (1px)
    entryLine := line.new(startBar, activeEntry, startBar + LINE_LOOKAHEAD, activeEntry,
         color = ENTRY_COLOR, style = line.style_dotted, width = 1)
    slLine    := line.new(startBar, activeSL,    startBar + LINE_LOOKAHEAD, activeSL,
         color = SL_COLOR,    style = line.style_solid,  width = 1)
    tp1Line   := line.new(startBar, activeTP1,   startBar + LINE_LOOKAHEAD, activeTP1,
         color = TP1_COLOR,   style = line.style_dashed, width = 1)
    tp2Line   := line.new(startBar, activeTP2,   startBar + LINE_LOOKAHEAD, activeTP2,
         color = TP2_COLOR,   style = line.style_dashed, width = 1)
    tp3Line   := line.new(startBar, activeTP3,   startBar + LINE_LOOKAHEAD, activeTP3,
         color = TP3_COLOR,   style = line.style_dashed, width = 1)

    if showSlTpLabelsInput
        // AFT-style: tinted background label matching line color, white text
        // % from entry shown when showPctOnLabelsInput is ON
        string entryStr = str.tostring(activeEntry, format.mintick)
        string slStr    = str.tostring(activeSL,    format.mintick) + formatPctFromEntry(activeSL,  activeEntry)
        string tp1Str   = str.tostring(activeTP1,   format.mintick) + formatPctFromEntry(activeTP1, activeEntry)
        string tp2Str   = str.tostring(activeTP2,   format.mintick) + formatPctFromEntry(activeTP2, activeEntry)
        string tp3Str   = str.tostring(activeTP3,   format.mintick) + formatPctFromEntry(activeTP3, activeEntry)

        entryLabel := label.new(startBar + LABEL_OFFSET, activeEntry, "ENTRY " + entryStr,
             color = ENTRY_COLOR, textcolor = LABEL_TEXT,
             style = label.style_label_left, size = labelSize)
        slLabel    := label.new(startBar + LABEL_OFFSET, activeSL,    "SL "  + slStr,
             color = SL_COLOR, textcolor = LABEL_TEXT,
             style = label.style_label_left, size = labelSize)
        tp1Label   := label.new(startBar + LABEL_OFFSET, activeTP1,   "TP1 " + tp1Str,
             color = TP1_COLOR, textcolor = LABEL_TEXT,
             style = label.style_label_left, size = labelSize)
        tp2Label   := label.new(startBar + LABEL_OFFSET, activeTP2,   "TP2 " + tp2Str,
             color = TP2_COLOR, textcolor = LABEL_TEXT,
             style = label.style_label_left, size = labelSize)
        tp3Label   := label.new(startBar + LABEL_OFFSET, activeTP3,   "TP3 " + tp3Str,
             color = TP3_COLOR, textcolor = LABEL_TEXT,
             style = label.style_label_left, size = labelSize)

    riskLinesActive := true

// ── TP hit visuals: line turns solid Pro-cyan, label gets ✓ ──
if showSlTpInput and not na(tp1Line) and tp1Reached
    line.set_color(tp1Line, TP_HIT_COLOR)
    line.set_style(tp1Line, line.style_solid)
    if showSlTpLabelsInput and not na(tp1Label)
        label.set_text(tp1Label, "TP1 ✓ " + str.tostring(activeTP1, format.mintick) + formatPctFromEntry(activeTP1, activeEntry))
        label.set_color(tp1Label, TP_HIT_COLOR)

if showSlTpInput and not na(tp2Line) and tp2Reached
    line.set_color(tp2Line, TP_HIT_COLOR)
    line.set_style(tp2Line, line.style_solid)
    if showSlTpLabelsInput and not na(tp2Label)
        label.set_text(tp2Label, "TP2 ✓ " + str.tostring(activeTP2, format.mintick) + formatPctFromEntry(activeTP2, activeEntry))
        label.set_color(tp2Label, TP_HIT_COLOR)

if showSlTpInput and not na(tp3Line) and tp3Reached
    line.set_color(tp3Line, TP_HIT_COLOR)
    line.set_style(tp3Line, line.style_solid)
    if showSlTpLabelsInput and not na(tp3Label)
        label.set_text(tp3Label, "TP3 ✓ " + str.tostring(activeTP3, format.mintick) + formatPctFromEntry(activeTP3, activeEntry))
        label.set_color(tp3Label, TP_HIT_COLOR)

// ── Break-even visualization ──
// When BE is active, original SL line is dimmed (still visible as reference,
// but no longer the "live" stop). The ENTRY label gets a "→ SL (BE)" suffix
// in amber, announcing where the effective stop now sits.
//
// Note: activeSL is logically moved to entry by the BE block above.
// Hit detection uses effectiveSL (captured before BE update), so SL hits
// resolve against the original SL on the bar of the move, then against
// entry on subsequent bars. The visible SL line and effective SL diverge
// here by design — the ENTRY label is the source of truth.
if showSlTpInput and beActive and activeDir != 0
    // Dim the original SL line — it stays visible as a reference but
    // is no longer the active stop.
    if not na(slLine)
        line.set_color(slLine, color.new(SL_COLOR, 70))
    if showSlTpLabelsInput
        if not na(slLabel)
            label.set_color(slLabel, color.new(SL_COLOR, 70))
        if not na(entryLabel)
            string entryStr = str.tostring(activeEntry, format.mintick)
            label.set_text(entryLabel, "ENTRY " + entryStr + "  → SL (BE)")
            label.set_color(entryLabel, BE_COLOR)

// Auto-reset visual lines once when position closes
if activeDir == 0 and riskLinesActive and showSlTpInput
    line.delete(slLine),     line.delete(tp1Line),  line.delete(tp2Line)
    line.delete(tp3Line),    line.delete(entryLine)
    label.delete(slLabel),   label.delete(tp1Label), label.delete(tp2Label)
    label.delete(tp3Label),  label.delete(entryLabel)
    riskLinesActive := false

// Extend lines to current bar while active
if activeDir != 0 and riskLinesActive and showSlTpInput
    int newEnd = bar_index + LINE_EXTEND
    if not na(slLine)
        line.set_x2(slLine,    newEnd)
        line.set_x2(tp1Line,   newEnd)
        line.set_x2(tp2Line,   newEnd)
        line.set_x2(tp3Line,   newEnd)
        line.set_x2(entryLine, newEnd)
    if showSlTpLabelsInput and not na(slLabel)
        int lblX = newEnd + 1
        label.set_x(slLabel,    lblX)
        label.set_x(tp1Label,   lblX)
        label.set_x(tp2Label,   lblX)
        label.set_x(tp3Label,   lblX)
        label.set_x(entryLabel, lblX)

// ══════════════════════════════════════════════════════════════════════════
// 21. DASHBOARD
// ══════════════════════════════════════════════════════════════════════════
// Aggregates: counts of pools by side and state
int activeBullPools = 0
int activeBearPools = 0
float strongestBull = 0.0
float strongestBear = 0.0
float strongestBullLevel = na
float strongestBearLevel = na
float nearestBullDist = 1e9
float nearestBearDist = 1e9
float nearestBullLevel = na
float nearestBearLevel = na

if showDashInput and barstate.islast and pools.size() > 0
    for i = 0 to pools.size() - 1
        LiquidityPool p = pools.get(i)
        if p.state == STATE_ACTIVE
            float s = p.computeStrength()
            if p.isHigh
                activeBearPools := activeBearPools + 1
                if s > strongestBear
                    strongestBear := s
                    strongestBearLevel := p.level
                float d = math.abs(p.level - close)
                if d < nearestBearDist and p.level > close
                    nearestBearDist := d
                    nearestBearLevel := p.level
            else
                activeBullPools := activeBullPools + 1
                if s > strongestBull
                    strongestBull := s
                    strongestBullLevel := p.level
                float d = math.abs(p.level - close)
                if d < nearestBullDist and p.level < close
                    nearestBullDist := d
                    nearestBullLevel := p.level

float currentRR  = activeDir != 0 ? effectiveTP1Mult : 0.0
float riskPctVal = activeDir != 0 and not na(activeEntry) and activeEntry != 0 and not na(activeSL) ? math.abs(activeEntry - activeSL) / activeEntry * 100.0 : 0.0

if showDashInput and barstate.islast
    var table dashTable = table.new(dashPos, 2, 18, TABLE_BG, TABLE_BORDER, 1, TABLE_BORDER, 1)

    // Header
    table.cell(dashTable, 0, 0, "💧 Liquidity Pools Pro", text_color = HEADER_TEXT, bgcolor = HEADER_BG, text_size = size.small, text_halign = text.align_center)
    table.merge_cells(dashTable, 0, 0, 1, 0)

    // Section: market state
    table.cell(dashTable, 0, 1, "Symbol",    text_color = TEXT_MUTED, text_size = size.small)
    table.cell(dashTable, 1, 1, syminfo.ticker, text_color = TEXT_COLOR, text_size = size.small)

    table.cell(dashTable, 0, 2, "TF",        text_color = TEXT_MUTED, text_size = size.small)
    table.cell(dashTable, 1, 2, timeframe.period, text_color = TEXT_COLOR, text_size = size.small)

    table.cell(dashTable, 0, 3, "Pivot Len", text_color = TEXT_MUTED, text_size = size.small, bgcolor = TABLE_ROW_ALT)
    table.cell(dashTable, 1, 3, str.tostring(leftLen) + (adaptivePivotInput ? " (adapt)" : ""), text_color = TEXT_COLOR, text_size = size.small, bgcolor = TABLE_ROW_ALT)

    // Section: pool stats
    table.cell(dashTable, 0, 4, "Active Pools",   text_color = TEXT_MUTED, text_size = size.small)
    table.cell(dashTable, 1, 4, str.tostring(activeBullPools + activeBearPools) + " (↑" + str.tostring(activeBullPools) + " ↓" + str.tostring(activeBearPools) + ")", text_color = TEXT_COLOR, text_size = size.small)

    table.cell(dashTable, 0, 5, "Strong SSL ↑",   text_color = TEXT_MUTED, text_size = size.small, bgcolor = TABLE_ROW_ALT)
    string ssrStr = na(strongestBearLevel) ? "—" : str.tostring(strongestBearLevel, format.mintick) + " [" + str.tostring(math.round(strongestBear)) + "]"
    table.cell(dashTable, 1, 5, ssrStr, text_color = BEAR_TEXT, text_size = size.small, bgcolor = TABLE_ROW_ALT)

    table.cell(dashTable, 0, 6, "Strong BSL ↓",   text_color = TEXT_MUTED, text_size = size.small)
    string bslStr = na(strongestBullLevel) ? "—" : str.tostring(strongestBullLevel, format.mintick) + " [" + str.tostring(math.round(strongestBull)) + "]"
    table.cell(dashTable, 1, 6, bslStr, text_color = BULL_TEXT, text_size = size.small)

    table.cell(dashTable, 0, 7, "Nearest Above",  text_color = TEXT_MUTED, text_size = size.small, bgcolor = TABLE_ROW_ALT)
    string nearAbove = na(nearestBearLevel) ? "—" : str.tostring(nearestBearLevel, format.mintick)
    table.cell(dashTable, 1, 7, nearAbove, text_color = BEAR_TEXT, text_size = size.small, bgcolor = TABLE_ROW_ALT)

    table.cell(dashTable, 0, 8, "Nearest Below",  text_color = TEXT_MUTED, text_size = size.small)
    string nearBelow = na(nearestBullLevel) ? "—" : str.tostring(nearestBullLevel, format.mintick)
    table.cell(dashTable, 1, 8, nearBelow, text_color = BULL_TEXT, text_size = size.small)

    // Diagnostics row — helps user understand if/why signals aren't firing
    string diagStr = "Mit:" + str.tostring(totalMitigationsCount) + " Sig:" + str.tostring(signalsFiredCount) + "/" + str.tostring(totalPoolsEverCount)
    color diagClr = signalsFiredCount > 0 ? BULL_TEXT : NEUTRAL_TEXT
    table.cell(dashTable, 0, 9, "Stats",  text_color = TEXT_MUTED, text_size = size.small)
    table.cell(dashTable, 1, 9, diagStr,  text_color = diagClr,    text_size = size.small)

    // Section: risk management
    string posStr   = activeDir == 1 ? "🟢 LONG" : activeDir == -1 ? "🔴 SHORT" : "FLAT"
    color  posClr   = activeDir == 1 ? BULL_TEXT : activeDir == -1 ? BEAR_TEXT : TEXT_MUTED
    table.cell(dashTable, 0, 10, "Position", text_color = TEXT_MUTED, text_size = size.small, bgcolor = TABLE_ROW_ALT)
    table.cell(dashTable, 1, 10, posStr,     text_color = posClr,     text_size = size.small, bgcolor = TABLE_ROW_ALT)

    // SL row: when BE is active, prefix with "BE @ " to clearly indicate
    // the stop has moved to entry — the value shown is now activeEntry.
    string slStrD  = activeDir != 0 and not na(activeSL)  ? (beActive ? "BE @ " + str.tostring(activeSL, format.mintick) : str.tostring(activeSL, format.mintick)) : "—"
    // TP rows: prefix with ✓ on first touch for visual confirmation
    string tp1StrD = activeDir != 0 and not na(activeTP1) ? (tp1Reached ? "✓ " : "") + str.tostring(activeTP1, format.mintick) : "—"
    string tp2StrD = activeDir != 0 and not na(activeTP2) ? (tp2Reached ? "✓ " : "") + str.tostring(activeTP2, format.mintick) : "—"
    string tp3StrD = activeDir != 0 and not na(activeTP3) ? (tp3Reached ? "✓ " : "") + str.tostring(activeTP3, format.mintick) : "—"
    string rrStrD  = activeDir != 0 ? str.tostring(currentRR, "#.#") + "R" : "—"
    string riskStrD = activeDir != 0 ? str.tostring(riskPctVal, "#.##") + "%" : "—"

    // Note: currentRR shows TP1 multiplier (planned R:R), not realized R:R
    color rrClr    = currentRR >= 2.0 ? BULL_TEXT : currentRR >= 1.0 ? NEUTRAL_TEXT : BEAR_TEXT
    color riskClr  = riskPctVal > 3.0 ? BEAR_TEXT  : riskPctVal > 1.0 ? NEUTRAL_TEXT : BULL_TEXT
    // SL color reflects state: amber when BE active, otherwise red
    color slDashColor = beActive ? BE_COLOR : SL_COLOR
    // TP colors brighten to TP_HIT_COLOR after reach
    color tp1DashColor = tp1Reached ? TP_HIT_COLOR : TP1_COLOR
    color tp2DashColor = tp2Reached ? TP_HIT_COLOR : TP2_COLOR
    color tp3DashColor = tp3Reached ? TP_HIT_COLOR : TP3_COLOR

    table.cell(dashTable, 0, 11, "SL",      text_color = TEXT_MUTED,  text_size = size.small)
    table.cell(dashTable, 1, 11, slStrD,    text_color = slDashColor, text_size = size.small)
    table.cell(dashTable, 0, 12, "TP1",     text_color = TEXT_MUTED,  text_size = size.small, bgcolor = TABLE_ROW_ALT)
    table.cell(dashTable, 1, 12, tp1StrD,   text_color = tp1DashColor, text_size = size.small, bgcolor = TABLE_ROW_ALT)
    table.cell(dashTable, 0, 13, "TP2",     text_color = TEXT_MUTED,  text_size = size.small)
    table.cell(dashTable, 1, 13, tp2StrD,   text_color = tp2DashColor, text_size = size.small)
    table.cell(dashTable, 0, 14, "TP3",     text_color = TEXT_MUTED,  text_size = size.small, bgcolor = TABLE_ROW_ALT)
    table.cell(dashTable, 1, 14, tp3StrD,   text_color = tp3DashColor, text_size = size.small, bgcolor = TABLE_ROW_ALT)
    table.cell(dashTable, 0, 15, "Plan R:R", text_color = TEXT_MUTED, text_size = size.small)
    table.cell(dashTable, 1, 15, rrStrD,    text_color = rrClr,      text_size = size.small)
    table.cell(dashTable, 0, 16, "Risk %",  text_color = TEXT_MUTED, text_size = size.small, bgcolor = TABLE_ROW_ALT)
    table.cell(dashTable, 1, 16, riskStrD,  text_color = riskClr,    text_size = size.small, bgcolor = TABLE_ROW_ALT)

    table.cell(dashTable, 0, 17, "Version " + INDICATOR_VERSION, text_color = TEXT_MUTED, text_size = size.tiny, text_halign = text.align_center)
    table.merge_cells(dashTable, 0, 17, 1, 17)

// ══════════════════════════════════════════════════════════════════════════
// 22. WATERMARK
// ══════════════════════════════════════════════════════════════════════════
if barstate.islast and showWatermarkInput
    var table wmTable = table.new(position.bottom_center, 1, 1,
         bgcolor      = color.new(color.black, 100),
         border_color = color.new(color.black, 100),
         border_width = 0,
         frame_color  = color.new(color.black, 100),
         frame_width  = 0)
    table.cell(wmTable, 0, 0, "WillyAlgoTrader",
         text_color  = WM_COLOR,
         text_size   = size.normal,
         text_halign = text.align_center,
         bgcolor     = color.new(color.black, 100))

// ══════════════════════════════════════════════════════════════════════════
// 23. ALERTS
// ══════════════════════════════════════════════════════════════════════════
string alertPrice  = str.tostring(close, format.mintick)
string alertSLStr  = str.tostring(nz(activeSL),  format.mintick)
string alertTP1Str = str.tostring(nz(activeTP1), format.mintick)
string alertTP2Str = str.tostring(nz(activeTP2), format.mintick)
string alertTP3Str = str.tostring(nz(activeTP3), format.mintick)
string alertEntryStr = str.tostring(nz(activeEntry), format.mintick)
string alertRRStr  = str.tostring(effectiveTP1Mult, "#.#")
string alertStrStr = str.tostring(nz(activePoolStrength), "#")

string jsonBuy  = '{"action":"buy","ticker":"' + syminfo.tickerid + '","tf":"' + timeframe.period + '","price":' + alertPrice + ',"sl":' + alertSLStr + ',"tp1":' + alertTP1Str + ',"tp2":' + alertTP2Str + ',"tp3":' + alertTP3Str + ',"rr":' + alertRRStr + ',"pool_strength":' + alertStrStr + '}'
string jsonSell = '{"action":"sell","ticker":"' + syminfo.tickerid + '","tf":"' + timeframe.period + '","price":' + alertPrice + ',"sl":' + alertSLStr + ',"tp1":' + alertTP1Str + ',"tp2":' + alertTP2Str + ',"tp3":' + alertTP3Str + ',"rr":' + alertRRStr + ',"pool_strength":' + alertStrStr + '}'

string textBuy  = "🟢 SWEEP BUY | "  + syminfo.tickerid + " | TF: " + timeframe.period + " | Price: " + alertPrice + " | Pool: " + alertStrStr + "/100 | SL: " + alertSLStr + " | TP1: " + alertTP1Str + " | TP2: " + alertTP2Str + " | TP3: " + alertTP3Str + " | R:R: " + alertRRStr
string textSell = "🔴 SWEEP SELL | " + syminfo.tickerid + " | TF: " + timeframe.period + " | Price: " + alertPrice + " | Pool: " + alertStrStr + "/100 | SL: " + alertSLStr + " | TP1: " + alertTP1Str + " | TP2: " + alertTP2Str + " | TP3: " + alertTP3Str + " | R:R: " + alertRRStr

if confirmedBuy
    alert(webhookInput ? jsonBuy : textBuy, alert.freq_once_per_bar_close)

if confirmedSell
    alert(webhookInput ? jsonSell : textSell, alert.freq_once_per_bar_close)

// SL hit
if slHit and alertSlHitInput and barstate.isconfirmed and activeDir != 0
    string slHitMsg = (beActive ? "🛡️ BE STOP-OUT | " : "🛑 SL HIT | ") + syminfo.tickerid + " | Entry: " + alertEntryStr + " | SL: " + alertSLStr
    alert(slHitMsg, alert.freq_once_per_bar_close)

// TP & BE alerts (use latched first-touch flags to fire only once per trade)
// Edge case: when TP1 + SL hit on the same bar, the trade is a loss — don't
// fire a misleading "TP1 HIT" notification. Same for TP2/TP3.
if tp1FirstTouch and not slHit and alertTpHitInput and barstate.isconfirmed
    alert("🎯 TP1 HIT | " + syminfo.tickerid + " | TP1: " + alertTP1Str, alert.freq_once_per_bar_close)

if beJustActivated and alertTpHitInput and barstate.isconfirmed
    alert("🛡️ BREAK-EVEN | " + syminfo.tickerid + " | SL moved to " + alertEntryStr, alert.freq_once_per_bar_close)

if tp2FirstTouch and not slHit and alertTpHitInput and barstate.isconfirmed
    alert("🎯 TP2 HIT | " + syminfo.tickerid + " | TP2: " + alertTP2Str, alert.freq_once_per_bar_close)

if tp3FirstTouch and not slHit and alertTpHitInput and barstate.isconfirmed
    alert("🏆 TP3 HIT | " + syminfo.tickerid + " | TP3: " + alertTP3Str, alert.freq_once_per_bar_close)

// ══════════════════════════════════════════════════════════════════════════
// END
// ══════════════════════════════════════════════════════════════════════════
