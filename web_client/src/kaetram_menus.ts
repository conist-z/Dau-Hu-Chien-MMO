// Kaetram ORIGINAL menus — ported 1:1 from Kaetram-Open
// packages/client/src/menu/*.ts (MPL-2.0, per project owner's decision to
// vendor the original UI). Each menu mirrors the original class: same DOM
// selectors, same show/hide/toggle behavior, same element wiring.
//
// Server-backed data our game doesn't have yet (quests, achievements,
// guilds, friends, warps, bank, trade, crafting) still builds the ORIGINAL
// structure (same element classes) and fills it with a clearly-labelled
// empty state — so when the server adds the system, only the data feed
// changes, not the UI.

// ---------- Menu base (menu.ts parity) ----------
export abstract class Menu {
  protected container: HTMLElement;
  protected close: HTMLElement;
  protected button: HTMLElement | null;
  /** Pending fadeOut timer — cancelled by fadeIn so a quick
   *  hide→show sequence can't close the freshly-opened menu
   *  (the "mở lên là tự tắt" bug). */
  private fadeTimer: number | null = null;
  /** Authoritative visibility flag — style.display lags behind the fade
   *  animation, so isVisible() must NOT read it. */
  private shown = false;

  constructor(containerName: string, closeButton?: string, toggleButton?: string) {
    this.container = document.querySelector(containerName)!;
    this.close = closeButton ? document.querySelector(closeButton)! : (null as unknown as HTMLElement);
    this.button = toggleButton ? document.querySelector(toggleButton) : null;

    this.close?.addEventListener("click", () => this.hide());
    this.button?.addEventListener("click", () => this.toggle());
  }

  /** Controller-level hook: opening one menu closes the others (Kaetram
   *  menus controller parity). Set once from KaetramMenus. */
  showCallback: (() => void) | null = null;

  public show(): void {
    this.showCallback?.();
    this.shown = true;
    this.button?.classList.add("active");
    this.fadeIn(this.container);
  }

  public hide(): void {
    this.shown = false;
    this.button?.classList.remove("active");
    this.fadeOut(this.container);
  }

  public toggle(): void {
    if (this.isVisible()) this.hide();
    else this.show();
  }

  public isVisible(): boolean {
    return this.shown;
  }

  /** Util.fadeIn parity — original uses opacity+display transitions. */
  protected fadeIn(el: HTMLElement): void {
    // Cancel any pending fadeOut — otherwise its timer fires after we've
    // re-shown and slams display back to none (auto-close bug).
    if (this.fadeTimer !== null) {
      window.clearTimeout(this.fadeTimer);
      this.fadeTimer = null;
    }
    el.style.removeProperty("display");
    // slice-container default is display:none in CSS; Kaetram's Util sets
    // display flex + animates opacity.
    const target = getComputedStyle(el).display === "none" ? "flex" : "";
    if (target) el.style.display = target;
    el.style.opacity = "0";
    requestAnimationFrame(() => {
      el.style.transition = "0.25s opacity linear";
      el.style.opacity = "1";
    });
  }

  protected fadeOut(el: HTMLElement): void {
    if (this.fadeTimer !== null) window.clearTimeout(this.fadeTimer);
    el.style.transition = "0.25s opacity linear";
    el.style.opacity = "0";
    this.fadeTimer = window.setTimeout(() => {
      this.fadeTimer = null;
      el.style.display = "none";
      el.style.removeProperty("opacity");
      el.style.removeProperty("transition");
    }, 250);
  }
}

// ---------- Quests (quests.ts parity) ----------
export interface QuestData {
  id: number;
  key: string;
  name: string;
  description: string; // 'short|long'
  rewards: string[];
  stage: number;
  stageCount: number;
  finished: boolean;
  started: boolean;
}

export class Quests extends Menu {
  private list: HTMLUListElement;
  private logsContainer: HTMLElement;
  private title: HTMLElement;
  private shortDescription: HTMLElement;
  private description: HTMLElement;
  private rewards: HTMLElement;
  private requirements: HTMLElement;

  constructor() {
    super("#quests", "#close-quests", "#quests-button");
    this.list = document.querySelector("#quests-list > ul")!;
    this.logsContainer = document.querySelector("#quests-logs-container")!;
    this.title = document.querySelector("#quests-logs-title")!;
    this.shortDescription = document.querySelector("#quests-logs-shortdesc")!;
    this.description = document.querySelector("#quests-logs-description")!;
    this.rewards = document.querySelector("#quests-logs-rewards")!;
    this.requirements = document.querySelector("#quests-logs-requirements")!;
  }

  /** Batch sync — same flow as quests.ts handle(Batch) + buildLog(first). */
  public batch(quests: QuestData[]): void {
    this.list.innerHTML = "";
    for (const quest of quests) this.createElement(quest);
    if (quests.length > 0) this.buildLog(quests[0]);
  }

  // handleProgress parity kept for when the server syncs quest progress
  // (Opcodes.Quest.Progress): updates row colour by completion.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  // handleProgress parity kept for when the server syncs quest progress
  // (Opcodes.Quest.Progress): updates row colour by completion.
  protected handleProgress(key: string, quests: Record<string, QuestData>): void {
    const quest = quests[key];
    if (!quest) return;
    const element = this.list.children[quest.id] as HTMLElement | undefined;
    const nameElement = element?.querySelector("p");
    if (!nameElement) return;
    nameElement.classList.remove("text-green", "text-yellow");
    if (quest.finished) nameElement.classList.add("text-green");
    else if (quest.started) nameElement.classList.add("text-yellow");
  }

  private createElement(quest: QuestData): void {
    const element = document.createElement("li");
    const name = document.createElement("p");
    element.classList.add("slice-list-item");
    name.innerHTML = quest.name;
    if (quest.finished) name.classList.add("text-green");
    else if (quest.started) name.classList.add("text-yellow");
    element.append(name);
    this.list.append(element);
    element.addEventListener("click", () => this.buildLog(quest));
  }

  private buildLog(quest: QuestData): void {
    this.logsContainer.scrollTop = 0;
    const [shortDescription, description] = quest.description.split("|");
    this.title.innerHTML = quest.name;
    this.shortDescription.innerHTML = shortDescription ?? "";
    this.description.innerHTML = description ?? "";
    if (quest.rewards) this.rewards.innerHTML = quest.rewards.join("<br>");
    this.requirements.innerHTML = "None";
  }
}

// ---------- Achievements (achievements.ts parity) ----------
export interface AchievementData {
  key: string;
  name: string;
  description: string;
  region: string;
  stage: number;
  stageCount: number;
  secret: boolean;
  finished: boolean;
  started: boolean;
}

export class Achievements extends Menu {
  private tabArrowLeft: HTMLDivElement;
  private tabArrowRight: HTMLDivElement;
  private tabText: HTMLSpanElement;
  private list: HTMLUListElement;

  private currentRegion = "Mudwich";
  private regions: string[] = [];
  private data: Record<string, AchievementData> = {};

  constructor() {
    super("#achievements", "#close-achievements", "#achievements-button");
    this.tabArrowLeft = document.querySelector("#achievements-tab-arrow-left")!;
    this.tabArrowRight = document.querySelector("#achievements-tab-arrow-right")!;
    this.tabText = document.querySelector("#achievements-tab-text")!;
    this.list = document.querySelector("#achievements-content > ul")!;
    this.tabArrowLeft.addEventListener("click", () => this.handleTabArrow(-1));
    this.tabArrowRight.addEventListener("click", () => this.handleTabArrow(1));
  }

  public batch(data: Record<string, AchievementData>): void {
    this.data = data;
    // Seed region list from the data (original receives regions via tasks).
    for (const key in data) {
      const region = data[key].region || "Miscellaneous";
      if (!this.regions.includes(region)) this.regions.push(region);
    }
    if (!this.regions.length) this.regions.push("Miscellaneous");
    this.displayAchievements();
  }

  private handleTabArrow(dir: number): void {
    let index = this.regions.indexOf(this.currentRegion);
    if (dir < 0) index = index === 0 ? this.regions.length - 1 : index - 1;
    else index = index === this.regions.length - 1 ? 0 : index + 1;
    this.currentRegion = this.regions[index];
    this.displayAchievements();
  }

  private displayAchievements(): void {
    this.list.innerHTML = "";
    for (const key in this.data) {
      const task = this.data[key];
      const region = task.region || "Miscellaneous";
      if (region === this.currentRegion) this.createAchievement(task, key);
    }
    this.tabText.innerHTML = ` ${this.currentRegion}`;
  }

  private createAchievement(task: AchievementData, key: string): void {
    const element = document.createElement("li");
    const slot = document.createElement("div");
    const coin = document.createElement("div");
    const title = document.createElement("p");
    const description = document.createElement("p");

    element.classList.add("achievement-element", "slice-list");
    slot.classList.add("coin-slot");
    title.classList.add("achievement-title");
    description.classList.add("achievement-description");

    title.innerHTML = task.name;
    description.innerHTML = task.description;
    slot.append(coin);
    element.append(slot, title, description);

    if (task.finished) {
      title.style.color = "#f4b41b";
      coin.classList.add(task.secret ? `coin-${key}` : "coin-default");
    } else if (task.started) element.append(this.createProgress(task));

    this.list.append(element);
  }

  private createProgress(task: AchievementData): HTMLElement {
    const progress = document.createElement("p");
    progress.classList.add("achievement-progress");
    progress.innerHTML = task.started ? `${task.stage - 1}/${task.stageCount - 1}` : "";
    return progress;
  }
}

// ---------- Settings (settings.ts parity, adapted to our engine knobs) ----------
export interface SettingsHooks {
  isDebug(): boolean;
  setDebug(on: boolean): void;
  showNamesEnabled(): boolean;
  setShowNames(on: boolean): void;
}

export class Settings extends Menu {
  private debugCheckbox: HTMLInputElement;
  private showNamesCheckbox: HTMLInputElement;
  private musicSlider: HTMLInputElement;
  private soundSlider: HTMLInputElement;
  private brightnessSlider: HTMLInputElement;

  constructor(private hooks: SettingsHooks) {
    super("#settings-page", "#close-settings", "#settings-button");
    this.musicSlider = document.querySelector("#music")!;
    this.soundSlider = document.querySelector("#sound")!;
    this.brightnessSlider = document.querySelector("#brightness")!;
    this.debugCheckbox = document.querySelector("#debug-mode-checkbox > input")!;
    this.showNamesCheckbox = document.querySelector("#show-names-checkbox > input")!;

    this.debugCheckbox.addEventListener("change", () => this.handleDebug());
    this.showNamesCheckbox.addEventListener("change", () => this.handleName());
    this.musicSlider.addEventListener("input", () => this.persist("music", this.musicSlider.valueAsNumber));
    this.soundSlider.addEventListener("input", () => this.persist("sound", this.soundSlider.valueAsNumber));
    this.brightnessSlider.addEventListener("input", () => {
      this.persist("brightness", this.brightnessSlider.valueAsNumber);
      this.applyBrightness();
    });

    document.querySelector("#logout-button")?.addEventListener("click", () => location.reload());

    this.load();
  }

  // localStorage parity with Kaetram's storage.getSettings/setX.
  private read(): Record<string, number | boolean> {
    try { return JSON.parse(localStorage.getItem("kaetram-settings") ?? "{}"); }
    catch { return {}; }
  }
  private persist(key: string, value: number | boolean): void {
    const s = this.read();
    s[key] = value;
    localStorage.setItem("kaetram-settings", JSON.stringify(s));
  }

  private load(): void {
    const s = this.read();
    this.musicSlider.value = String(s.music ?? 100);
    this.soundSlider.value = String(s.sound ?? 100);
    this.brightnessSlider.value = String(s.brightness ?? 100);
    this.debugCheckbox.checked = this.hooks.isDebug();
    this.showNamesCheckbox.checked = this.hooks.showNamesEnabled();
    this.applyBrightness();
  }

  private handleDebug(): void {
    this.hooks.setDebug(this.debugCheckbox.checked);
  }

  private handleName(): void {
    this.hooks.setShowNames(this.showNamesCheckbox.checked);
  }

  /** Brightness parity: Kaetram sets renderer.setBrightness — we tint the
   *  game canvas with a CSS filter (same visual effect, no engine change). */
  private applyBrightness(): void {
    const v = this.brightnessSlider.valueAsNumber; // 0..100
    const canvas = document.querySelector("#game-root canvas") as HTMLElement | null;
    if (canvas) canvas.style.filter = v >= 100 ? "" : `brightness(${0.4 + (v / 100) * 0.6})`;
  }
}

// ---------- Leaderboards (leaderboards.ts parity — offline variant) ----------
export interface LeaderboardRow { name: string; info: string }

export class Leaderboards extends Menu {
  private searchList: HTMLUListElement;
  private resultsList: HTMLUListElement;
  private search: HTMLInputElement;

  constructor(private sources: { name: string; rows: LeaderboardRow[] }[]) {
    super("#leaderboards", "#close-leaderboards", "#leaderboard-button");
    this.searchList = document.querySelector("#leaderboards-search > ul")!;
    this.resultsList = document.querySelector("#leaderboards-results > ul")!;
    this.search = document.querySelector("#leaderboards-search-input")!;
    this.search.addEventListener("input", () => this.handleInput());

    this.load();
  }

  private load(): void {
    this.searchList.innerHTML = "";
    for (const element of this.sources) this.createSearchElement(element);
    if (this.sources.length > 0) this.handleSearchElement(this.sources[0]);
  }

  private handleInput(): void {
    const input = this.search.value.toLowerCase();
    for (const element of this.searchList.children) {
      const name = element.querySelector("p")!.innerHTML.toLowerCase();
      (element as HTMLElement).hidden = !name.includes(input);
    }
  }

  private handleSearchElement(source: { name: string; rows: LeaderboardRow[] }): void {
    this.resultsList.innerHTML = "";
    for (const result of source.rows) this.createResultElement(result);
  }

  private createSearchElement(source: { name: string; rows: LeaderboardRow[] }): void {
    const element = document.createElement("li");
    const name = document.createElement("p");
    element.classList.add("slice-list-item");
    name.classList.add("stroke", "white");
    name.innerHTML = source.name;
    element.append(name);
    element.addEventListener("click", () => this.handleSearchElement(source));
    this.searchList.append(element);
  }

  private createResultElement(result: LeaderboardRow): void {
    const element = document.createElement("li");
    const name = document.createElement("p");
    const info = document.createElement("p");
    element.classList.add("slice-list-item");
    name.innerHTML = result.name;
    info.innerHTML = result.info;
    element.append(name, info);
    this.resultsList.append(element);
  }
}

// ---------- Warp / map frame (warp.ts parity) ----------
export class Warp extends Menu {
  private list: NodeListOf<HTMLElement>;

  constructor(private onSelectCallback?: (id: number) => void) {
    super("#map-frame", "#close-map-frame", "#warp-button");
    this.list = document.querySelectorAll(".map-button")!;
    for (const element of this.list)
      element.addEventListener("click", () => this.handleWarp(element));
  }

  private handleWarp(element: HTMLElement): void {
    const id = parseInt(element.id.replace("warp", ""));
    if (isNaN(id)) return;
    this.onSelectCallback?.(id);
    this.hide();
  }
}

// ---------- Equipments (equipments.ts parity — display + stats) ----------
export interface EquipmentDisplay {
  // slot key -> { iconUrl, count? } (undefined = unequipped placeholder)
  slots: Partial<Record<string, { iconUrl: string; count?: number }>>;
  attack: Record<string, number | string>;
  defense: Record<string, number | string>;
  bonuses: Record<string, number | string>;
}

const STAT_ROWS: [string, string[]][] = [
  ["#attack-stats", [".crush", ".slash", ".stab", ".archery", ".magic"]],
  ["#defense-stats", [".crush", ".slash", ".stab", ".archery", ".magic"]],
  ["#bonuses", [".accuracy", ".strength", ".archery", ".magic"]],
];

export class Equipments extends Menu {
  private slots: Record<string, HTMLElement> = {};
  private unequipCallback?: (slot: string) => void;

  constructor() {
    super("#equipments", "#close-equipments", "#equipment-button");
    for (const key of [
      "helmet", "pendant", "arrows", "chestplate", "weapon", "shield",
      "ring", "armour-skin", "weapon-skin", "legplates", "cape", "boots",
    ]) {
      const el = document.querySelector(
        `.equipment-slot-${key} > .equipment-slot-image`,
      ) as HTMLElement | null;
      if (el) this.slots[key] = el;
    }
    // Unequip on click (original behavior — server decides if allowed).
    for (const [key, el] of Object.entries(this.slots))
      el.addEventListener("click", () => this.unequipCallback?.(key));
    // Placeholder icons for every unequipped slot (Kaetram's
    // Util.getEquipmentPlaceholderURL parity) — the bare page looked
    // "empty/broken"; with placeholders it reads as the real paperdoll.
    this.showPlaceholders();
    // Stats render as "Crush: 0" etc. instead of bare labels until the
    // server provides real bonuses.
    this.fillStats("#attack-stats", {});
    this.fillStats("#defense-stats", {});
    this.fillStats("#bonuses", {});
  }

  /** Fill every slot with its grey placeholder icon. */
  private showPlaceholders(): void {
    for (const [key, el] of Object.entries(this.slots))
      el.style.backgroundImage =
        `url("/ui/kaetram/interface/equipment/${key}.png")`;
  }

  public onUnequip(cb: (slot: string) => void): void {
    this.unequipCallback = cb;
  }

  /** synchronize() parity: fill slot images + stat rows from display data.
   *  Slots without data fall back to their placeholder icon. */
  public synchronize(display: EquipmentDisplay): void {
    for (const [key, el] of Object.entries(this.slots)) {
      const data = display.slots[key.replace("-skin", "Skin") as string] ?? display.slots[key];
      el.style.backgroundImage =
        data?.iconUrl ?? `url("/ui/kaetram/interface/equipment/${key}.png")`;
    }
    const countEl = document.querySelector(".equipment-slot-arrows-count");
    const arrows = display.slots["arrows"];
    if (countEl) countEl.textContent = arrows?.count ? `${arrows.count}` : "";
    this.fillStats("#attack-stats", display.attack);
    this.fillStats("#defense-stats", display.defense);
    this.fillStats("#bonuses", display.bonuses);
  }

  private fillStats(rootSel: string, values: Record<string, number | string>): void {
    const root = document.querySelector(rootSel);
    if (!root) return;
    for (const [sel, labels] of STAT_ROWS) {
      if (sel !== rootSel) continue;
      for (const label of labels) {
        const el = root.querySelector(label);
        const key = label.replace(".", "");
        if (el) el.innerHTML = `${el.innerHTML.split(":")[0]}: ${values[key] ?? "0"}`;
      }
    }
  }
}

// ---------- KaetramMenus controller (controllers/menus parity) ----------
/** Shows one menu at a time; hideOnShow parity. */
export class KaetramMenus {
  private menus: Menu[] = [];

  public register(menu: Menu): void {
    menu.showCallback = () => {
      for (const other of this.menus)
        if (other !== menu && other.isVisible()) other.hide();
    };
    this.menus.push(menu);
  }
}
