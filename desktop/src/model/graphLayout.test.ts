import { describe, expect, it } from "vitest";
import {
  SPARSE_LAYOUT_MAX_NODES,
  TIER_PALETTE,
  hierarchyTiers,
  sparseLayoutPositions,
  tierColor,
  tierLabel,
} from "./graphLayout";

const edge = (source: string, target: string, relation = "contains") => ({ source, target, relation });

describe("hierarchyTiers", () => {
  it("puts roots at tier 0 and children below their parent", () => {
    const tiers = hierarchyTiers(["family", "a", "b"], [edge("family", "a"), edge("family", "b")]);
    expect(tiers.get("family")).toBe(0);
    expect(tiers.get("a")).toBe(1);
    expect(tiers.get("b")).toBe(1);
  });

  it("supports three levels", () => {
    // 实测依赖：capability_family contains capability；再往下（域 contains 族）就是三级。
    const tiers = hierarchyTiers(
      ["domain", "family", "capability"],
      [edge("domain", "family"), edge("family", "capability")],
    );
    expect(tiers.get("domain")).toBe(0);
    expect(tiers.get("family")).toBe(1);
    expect(tiers.get("capability")).toBe(2);
  });

  it("ignores depends_on — 依赖是跨层调用，不是包含关系", () => {
    // 若把 depends_on 也算进层级，整张网会被拍平：能力之间的调用会让双方互相拉低层级。
    const tiers = hierarchyTiers(["family", "a", "b"], [edge("family", "a"), edge("a", "b", "depends_on")]);
    expect(tiers.get("a")).toBe(1); // 由 contains 决定
    expect(tiers.get("b")).toBe(0); // 没有 contains 父 → 就是根；depends_on 不参与分层
  });

  it("gives every node a tier even when it has no parent", () => {
    const tiers = hierarchyTiers(["lonely"], []);
    expect(tiers.get("lonely")).toBe(0);
  });

  it("terminates on cycles instead of recursing forever", () => {
    const tiers = hierarchyTiers(["a", "b"], [edge("a", "b"), edge("b", "a")]);
    expect(tiers.size).toBe(2);
    for (const value of tiers.values()) expect(Number.isFinite(value)).toBe(true);
  });

  it("ignores self loops", () => {
    const tiers = hierarchyTiers(["a"], [edge("a", "a")]);
    expect(tiers.get("a")).toBe(0);
  });

  it("is deterministic when a node has several parents", () => {
    const edges = [edge("p2", "child"), edge("p1", "child")];
    const first = hierarchyTiers(["p1", "p2", "child"], edges);
    const second = hierarchyTiers(["p1", "p2", "child"], [...edges].reverse());
    expect(first.get("child")).toBe(second.get("child"));
  });
});

describe("tierColor / tierLabel", () => {
  it("gives each of the first four tiers a distinct colour", () => {
    const colors = [0, 1, 2, 3].map(tierColor);
    expect(new Set(colors).size).toBe(4);
  });

  it("clamps beyond the palette instead of leaving a node colourless", () => {
    expect(tierColor(9)).toBe(TIER_PALETTE[TIER_PALETTE.length - 1]);
  });

  it("names the first three tiers in Chinese", () => {
    expect([0, 1, 2].map(tierLabel)).toEqual(["一级", "二级", "三级"]);
  });
});

describe("sparseLayoutPositions", () => {
  const WIDTH = 1418;
  const HEIGHT = 570;

  it("centres a lone node", () => {
    expect(sparseLayoutPositions(1, WIDTH, HEIGHT)).toEqual([{ x: WIDTH / 2, y: HEIGHT / 2 }]);
  });

  it("lays two nodes out horizontally, not as a stretched vertical line", () => {
    // ECharts 的 circular 布局会把两个节点放到圆的上下两极，在超宽画布上拉成一根
    // 几百像素的竖线 —— 这条断言把"改成左右排布"钉住。
    const [first, second] = sparseLayoutPositions(2, WIDTH, HEIGHT);
    expect(first.y).toBe(second.y);
    expect(Math.abs(first.x - second.x)).toBeGreaterThan(WIDTH * 0.5);
    expect(Math.min(first.x, second.x)).toBeLessThan(WIDTH / 2);
    expect(Math.max(first.x, second.x)).toBeGreaterThan(WIDTH / 2);
  });

  it("spreads nodes symmetrically around the centre", () => {
    const points = sparseLayoutPositions(4, WIDTH, HEIGHT);
    const meanX = points.reduce((sum, point) => sum + point.x, 0) / points.length;
    const meanY = points.reduce((sum, point) => sum + point.y, 0) / points.length;
    expect(Math.round(meanX)).toBe(Math.round(WIDTH / 2));
    expect(Math.round(meanY)).toBe(Math.round(HEIGHT / 2));
  });

  it("keeps every node inside the canvas for the sparse range", () => {
    for (let count = 1; count <= SPARSE_LAYOUT_MAX_NODES; count++) {
      for (const point of sparseLayoutPositions(count, WIDTH, HEIGHT)) {
        expect(point.x).toBeGreaterThanOrEqual(0);
        expect(point.x).toBeLessThanOrEqual(WIDTH);
        expect(point.y).toBeGreaterThanOrEqual(0);
        expect(point.y).toBeLessThanOrEqual(HEIGHT);
      }
    }
  });

  it("returns exactly one position per node", () => {
    for (const count of [1, 2, 3, 7, SPARSE_LAYOUT_MAX_NODES]) {
      expect(sparseLayoutPositions(count, WIDTH, HEIGHT)).toHaveLength(count);
    }
  });
});
