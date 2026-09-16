// Kaetram-parity VERTICAL hub bar (user spec: xoay dọc, sát mép phải,
// ngay bên trên khung chat) + the shared page container it toggles.
//
// Sprite mapping (measured from the Kaetram-Open repo — scss/game/impl/
// _buttons.scss + abstracts/_sprite.scss):
//   hud_buttons.png is 66x250 = 3 COLUMNS of 10 ROWS. Each row is one
//   button, each column a STATE: x=0 rest, x=22 hover, x=44 active.
//   Row order (the exact $selectors list order):
//     0 inventory, 1 chat, 2 leaderboard, 3 warp(map), 4 settings,
//     5 profile, 6 quests, 7 guilds, 8 friends, 9 achievements.
//   Cell: 22x25 kit px, drawn at scale 2 (44x50 css) like Kaetram.
//
// Pages use Kaetram's own 9-slice kit (slices/container.png etc.) via
// border-image — the same CSS technique Kaetram's _slice.scss uses.
//
// Pages ship with FULL Kaetram logic layout but server-backed data only
// where the game already has it (self stats, map, players, crafting
// catalog); the rest render as clearly-labelled placeholder lists.

export type HubPageId =
  | "map" | "profile" | "equipment" | "settings" | "quests"
  | "achievements" | "players" | "guilds" | "friends"
  | "chat" | "inventory";

const BTN_W = 22;
const BTN_H = 25;
const SCALE = 2;
const SHEET_W = 66; // 3 states x 22

/** Row index per page id (hud_buttons row order — verified from
 *  Kaetram's _buttons.scss $selectors list). */
const ROW_BY_PAGE: Record<string, number> = {
  inventory: 0, chat: 1, leaderboard: 2, map: 3, settings: 4,
  profile: 5, quests: 6, guilds: 7, friends: 8, achievements: 9,
};
// Bar buttons top→bottom: Kaetram's visual order, minus inventory (the
// client already owns the bag panel — its button routes there) and chat
// (routes to the chat input). The vertical bar reads top-to-bottom.
const BAR_PAGES: HubPageId[] = [
  "map", "profile", "equipment", "quests", "achievements",
  "guilds", "friends", "chat", "inventory", "settings",
];

interface BarButton {
  page: HubPageId;
  el: HTMLDivElement;
}

export class HubBar {
  private bar: HTMLDivElement;
  private container: HTMLDivElement;
  private pageBody: HTMLDivElement;
  private pageTitle: HTMLElement;
  private pageClose: HTMLDivElement;
  private buttons: BarButton[] = [];
  /** Currently open page, or null (container hidden). */
  private openPage: HubPageId | null = null;
  /** Set by ui.ts/main.ts: handle clicks the simple hub cannot (inventory). */
  onPage: ((page: HubPageId) => boolean) | null = null;
  /** Called AFTER a page opens (page container visible) — ui.ts wires this
   *  to HubPages.render() so the body always shows fresh data. Without it
   *  the body stays EMPTY (the click-path bug found in testing). */
  onOpen: ((page: HubPageId) => void) | null = null;
  /** Settings → Debug mode (F3 parity). */
  onToggleDebug: (() => void) | null = null;
  /** Settings → Log out (reload, mirrors Kaetram's settings page). */
  onLogout: (() => void) | null = null;

  constructor() {
    const root = document.getElementById("hud-hub")!;
    // --- vertical bar pinned right edge, above the chat frame ---
    // Hidden until the player joins (ui.hideGate reveals it) — the bar
    // must never float over the login gate / lobby.
    this.bar = document.createElement("div");
    this.bar.id = "hub-bar";
    this.bar.classList.add("hidden");
    for (const page of BAR_PAGES) {
      const btn = this.makeButton(page);
      this.buttons.push({ page, el: btn });
      this.bar.appendChild(btn);
    }
    root.appendChild(this.bar);

    // --- shared page container (Kaetram slice-container clone) ---
    this.container = document.createElement("div");
    this.container.id = "hub-page";
    this.container.classList.add("hidden");
    this.container.innerHTML = `
      <div class="hub-page-head">
        <span id="hub-page-title"></span>
        <div id="hub-page-close" title="Đóng"></div>
      </div>
      <div class="hub-page-body hub-slice-inner"></div>
      <div class="hub-page-foot hub-slice-inner" id="hub-page-foot"></div>`;
    this.pageTitle = this.container.querySelector("#hub-page-title")!;
    this.pageBody = this.container.querySelector(".hub-page-body")!;
    this.pageClose = this.container.querySelector("#hub-page-close")!;
    this.container
      .querySelector(".hub-page-foot")!
      .addEventListener("click", (e) => {
        const t = (e.target as HTMLElement).closest("[data-hub-action]");
        if (!t) return;
        const action = (t as HTMLElement).dataset.hubAction;
        if (action === "debug") this.onToggleDebug?.();
        else if (action === "logout") this.onLogout?.();
      });
    this.pageClose.addEventListener("click", () => this.close());
    root.appendChild(this.container);
  }

  private makeButton(page: HubPageId): HTMLDivElement {
    const el = document.createElement("div");
    el.className = "hub-btn";
    el.style.width = `${BTN_W * SCALE}px`;
    el.style.height = `${BTN_H * SCALE}px`;
    el.style.backgroundImage = "url('/ui/kaetram/interface/hud_buttons.png')";
    el.style.backgroundRepeat = "no-repeat";
    el.style.backgroundSize = `${SHEET_W * SCALE}px ${250 * SCALE}px`;
    this.applyState(el, page, false);
    el.title = PAGE_TITLES[page];
    el.addEventListener("click", () => this.click(page));
    return el;
  }

  /** background-position for (state, row): rest=0, hover=1, active=2. */
  private applyState(el: HTMLDivElement, page: HubPageId, active: boolean): void {
    const col = active ? 2 : 0;
    const row = ROW_BY_PAGE[page];
    el.style.backgroundPosition = `-${col * BTN_W * SCALE}px -${row * BTN_H * SCALE}px`;
  }

  private click(page: HubPageId): void {
    // Custom handlers (inventory → bag panel, chat → focus chat input).
    if (this.onPage?.(page) === true) return;
    if (this.openPage === page) {
      this.close();
      return;
    }
    this.open(page);
  }

  /** Open (or switch to) a page. One page at a time — opening one closes
   *  the previous (Kaetram menu.onShow parity). */
  open(page: HubPageId): void {
    if (this.openPage !== null) {
      this.setActive(this.openPage, false);
    }
    this.openPage = page;
    this.setActive(page, true);
    this.pageTitle.textContent = PAGE_TITLES[page];
    this.container.classList.remove("hidden");
    // Page content is data-driven: let the owner (HubPages) paint it.
    this.onOpen?.(page);
  }

  close(): void {
    if (this.openPage === null) return;
    this.setActive(this.openPage, false);
    this.openPage = null;
    this.container.classList.add("hidden");
  }

  get current(): HubPageId | null {
    return this.openPage;
  }

  setActive(page: HubPageId, active: boolean): void {
    const btn = this.buttons.find((b) => b.page === page);
    if (btn) this.applyState(btn.el, page, active);
  }

  /** Replace the page body content (caller builds the DOM). */
  setBody(content: HTMLElement): void {
    this.pageBody.replaceChildren(content);
  }

  /** Extra footer rows (settings buttons, game info…). Empty = hidden. */
  setFoot(content: HTMLElement | null): void {
    const foot = this.container.querySelector<HTMLElement>(".hub-page-foot")!;
    foot.classList.toggle("hidden", !content || !content.childElementCount);
    if (content) foot.replaceChildren(...content.children);
  }

  get isOpen(): boolean {
    return this.openPage !== null;
  }

  /** Reveal the bar once the player is in-game (gate/lobby hidden). */
  show(): void {
    this.bar.classList.remove("hidden");
  }
}

export const PAGE_TITLES: Record<HubPageId, string> = {
  map: "Bản đồ",
  profile: "Hồ sơ",
  equipment: "Trang bị",
  settings: "Cài đặt",
  quests: "Nhiệm vụ",
  achievements: "Thành tựu",
  players: "Người chơi",
  guilds: "Bang hội",
  friends: "Bạn bè",
  chat: "Chat",
  inventory: "Túi đồ",
};
