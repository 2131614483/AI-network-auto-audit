import { afterEach, beforeEach, expect, it, vi } from "vitest";

const fixture = vi.hoisted(() => ({
  windows: [] as Array<{ destroyed: boolean; close(): void }>,
  quit: vi.fn(), error: vi.fn(),
  readZoom: vi.fn(), releaseZoom: undefined as undefined | ((value: string) => void),
}));

vi.mock("./backend", () => ({ startPackagedBackend: vi.fn().mockResolvedValue({ apiBaseUrl: "http://127.0.0.1:9" }),
  stopPackagedBackend: vi.fn().mockResolvedValue(undefined), approvePackagedUpload: vi.fn(), enablePackagedPlanning: vi.fn() }));
vi.mock("node:fs", async importOriginal => ({ ...await importOriginal<typeof import("node:fs")>(),
  existsSync: () => false, mkdirSync: vi.fn(), writeFileSync: vi.fn(), appendFileSync: vi.fn(), readdirSync: () => [] }));
vi.mock("node:fs/promises", () => ({ readFile: (...args: unknown[]) => fixture.readZoom(...args),
  writeFile: vi.fn().mockResolvedValue(undefined), readdir: vi.fn(), stat: vi.fn() }));
vi.mock("electron", async () => {
  const { EventEmitter } = await import("node:events");
  const app = Object.assign(new EventEmitter(), { isPackaged: true, whenReady: () => Promise.resolve(),
    requestSingleInstanceLock: () => true, getPath: () => "C:/synthetic-window-test", quit: fixture.quit });
  class Window extends EventEmitter {
    destroyed = false;
    webContents = Object.assign(new EventEmitter(), { send: vi.fn(), setZoomFactor: vi.fn(),
      executeJavaScript: vi.fn().mockResolvedValue(undefined) });
    constructor() { super(); fixture.windows.push(this); }
    isDestroyed() { return this.destroyed; }
    setMenuBarVisibility() {}
    loadFile() { return Promise.resolve(); }
    maximize() {}
    close() {
      this.destroyed = true; this.emit("closed");
      if (!fixture.windows.some(window => !window.destroyed)) app.emit("window-all-closed");
    }
    static getAllWindows() { return fixture.windows.filter(window => !window.destroyed); }
  }
  return { app, BrowserWindow: Window, ipcMain: { handle: vi.fn(), on: vi.fn() },
    Menu: { buildFromTemplate: vi.fn(), setApplicationMenu: vi.fn() },
    dialog: { showErrorBox: fixture.error } };
});

const originalConsole = { ...console };
beforeEach(() => {
  vi.resetModules(); fixture.windows = []; fixture.quit.mockClear(); fixture.error.mockClear();
  fixture.readZoom.mockImplementation(() => new Promise<string>(resolve => { fixture.releaseZoom = resolve; }));
  vi.stubGlobal("setInterval", vi.fn());
});
afterEach(() => { Object.assign(console, originalConsole); vi.unstubAllGlobals(); });

it("keeps startup alive during delayed profile loading and hands off to the real main entry's workspace window", async () => {
  await import("./main");
  await vi.waitFor(() => expect(fixture.releaseZoom).toBeTypeOf("function"));
  expect(fixture.error).not.toHaveBeenCalled();
  expect(fixture.quit).not.toHaveBeenCalled();
  expect(fixture.windows.filter(window => !window.destroyed)).toHaveLength(1);
  fixture.releaseZoom!('{"zoomFactor":1}');
  await vi.waitFor(() => expect(fixture.windows).toHaveLength(2));
  expect(fixture.windows[0].destroyed).toBe(true);
  expect(fixture.windows[1].destroyed).toBe(false);
  expect(fixture.quit).not.toHaveBeenCalled();
  fixture.windows[1].close();
  expect(fixture.quit).toHaveBeenCalledOnce();
});
