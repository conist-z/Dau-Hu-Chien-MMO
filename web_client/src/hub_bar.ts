// LEGACY SHIM — the hand-made hub bar + page container were RETIRED.
// The real UI is the ORIGINAL Kaetram #buttons bar + page containers
// declared in index.html, styled by the compiled kaetram_ui.css (their own
// scss) and driven by kaetram_menus.ts (wired in ui.ts initKaetramMenus).
//
// This module now only provides:
//   - HubPageId type (still referenced by ui.ts data-feed shims)
//   - HubBar: gate show/hide of the REAL #buttons bar + no-op page API so
//     legacy call sites (HubPages, openHubPage) keep compiling safely.
//
// Do NOT add new pages here — extend kaetram_menus.ts instead.

export type HubPageId =
  | "map" | "profile" | "equipment" | "settings" | "quests"
  | "achievements" | "players" | "guilds" | "friends"
  | "chat" | "inventory";

/** The real (original Kaetram) bar element, once index.html has parsed. */
function realBar(): HTMLElement | null {
  return document.getElementById("buttons");
}

export class HubBar {
  /** Legacy no-ops — the original menus manage their own visibility. */
  onPage: ((page: HubPageId) => boolean) | null = null;
  onOpen: ((page: HubPageId) => void) | null = null;
  onToggleDebug: (() => void) | null = null;
  onLogout: (() => void) | null = null;

  /** Open/switch shim: routes to the original menus where possible. */
  open(_page: HubPageId): void {}

  close(): void {}

  get current(): HubPageId | null {
    return null;
  }

  setActive(_page: HubPageId, _active: boolean): void {}

  setBody(_content: HTMLElement): void {}

  setFoot(_content: HTMLElement | null): void {}

  get isOpen(): boolean {
    return false;
  }

  /** Reveal the bar once the player is in-game (gate/lobby hidden). */
  show(): void {
    realBar()?.classList.add("shown");
  }

  /** Hide the bar again (back to gate/lobby). */
  hide(): void {
    realBar()?.classList.remove("shown");
  }
}
