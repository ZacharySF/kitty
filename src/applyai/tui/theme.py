"""The dark-fantasy look: a blood-red sky over a black keep, storm-grey stone,
a moss-dark forest, torchlight and old bone.

Every colour the TUI uses is named here once. Table cells are rendered with
Rich markup, which cannot read Textual theme variables, so screens use these
names directly; the stylesheet uses the theme variables built from the same
values. Each colour also carries one meaning everywhere (see the comments), so
the look stays easy to read: ember = what you can act on, moss = good,
gold = look closer, crimson = problem, ash = done or unimportant.
"""

from __future__ import annotations

from textual.theme import Theme

NIGHT = "#0d0909"      # the keep's silhouette: app background
CRYPT = "#161011"      # surfaces
VAULT = "#231a1b"      # panels, table headers
BLOOD = "#a4231a"      # the red sky: primary, the current tab, the cursor row
EMBER = "#e07a2e"      # torchlight: keys and anything you can act on
GOLD = "#d4a64a"       # old gold: look closer (ambiguous, OA, tier A)
STORM = "#8a96a3"      # storm-grey stone: neutral information (applied)
DUSK = "#b48ccf"       # twilight over the hills: interviews
MOSS = "#86a95f"       # the forest: good news (eligible, offer, ok)
CRIMSON = "#e0473b"    # fresh blood, bright enough to read on black: problems
BONE = "#e3d6bf"       # text
ASH = "#6b6260"        # muted: closed, ghosted, never run

DARK_FANTASY = Theme(
    name="dark-fantasy",
    primary=BLOOD,
    secondary=STORM,
    accent=EMBER,
    warning=GOLD,
    error=CRIMSON,
    success=MOSS,
    foreground=BONE,
    background=NIGHT,
    surface=CRYPT,
    panel=VAULT,
    dark=True,
    variables={
        "footer-background": CRYPT,
        "footer-key-foreground": EMBER,
        "footer-description-foreground": BONE,
        "block-cursor-background": BLOOD,
        "block-cursor-foreground": BONE,
        "block-cursor-text-style": "bold",
        "block-hover-background": f"{BLOOD} 25%",
        "input-selection-background": f"{BLOOD} 50%",
        "scrollbar": VAULT,
        "scrollbar-hover": BLOOD,
        "scrollbar-active": EMBER,
        "scrollbar-background": NIGHT,
        "link-color": BONE,
        "link-color-hover": BONE,
        "link-background-hover": BLOOD,
        "link-style": "none",
    },
)
