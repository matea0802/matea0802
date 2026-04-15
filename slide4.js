const pptxgen = require("pptxgenjs");

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9";

const slide = pres.addSlide();

// ── 배경 ──────────────────────────────────────
slide.background = { color: "0D1117" };

// ── 상단 타이틀 영역 ──────────────────────────
slide.addShape(pres.shapes.RECTANGLE, {
  x: 0, y: 0, w: 10, h: 1.05,
  fill: { color: "161B22" },
  line: { color: "161B22" }
});

// 좌측 강조 바
slide.addShape(pres.shapes.RECTANGLE, {
  x: 0, y: 0, w: 0.06, h: 1.05,
  fill: { color: "F96167" },
  line: { color: "F96167" }
});

slide.addText("핵심 알고리즘  —  감도 판정 기준", {
  x: 0.25, y: 0, w: 9.5, h: 1.05,
  fontSize: 26, bold: true, color: "F0F6FC",
  fontFace: "Arial", valign: "middle", margin: 0
});

// ── 판정 기준 카드 3개 ──────────────────────────
// 빠름 카드
slide.addShape(pres.shapes.RECTANGLE, {
  x: 0.4, y: 1.25, w: 9.2, h: 1.1,
  fill: { color: "1C1018" },
  line: { color: "FF4444", pt: 1.5 }
});
// 빠름 왼쪽 태그
slide.addShape(pres.shapes.RECTANGLE, {
  x: 0.4, y: 1.25, w: 1.1, h: 1.1,
  fill: { color: "FF4444" },
  line: { color: "FF4444" }
});
slide.addText("빠름", {
  x: 0.4, y: 1.25, w: 1.1, h: 1.1,
  fontSize: 22, bold: true, color: "FFFFFF",
  fontFace: "Arial", align: "center", valign: "middle", margin: 0
});

// 빠름 조건 텍스트
slide.addText([
  { text: "진동 비율", options: { color: "FF8888", bold: true } },
  { text: "  >  8%    ", options: { color: "FFCCCC" } },
  { text: "AND", options: { color: "888888", bold: true } },
  { text: "    평균 오차", options: { color: "FF8888", bold: true } },
  { text: "  >  6 px", options: { color: "FFCCCC" } },
], {
  x: 1.65, y: 1.25, w: 5.5, h: 1.1,
  fontSize: 18, fontFace: "Arial", valign: "middle", margin: 0
});

// 빠름 설명
slide.addText("커서가 타겟을 자주 지나침 (오버슈팅)", {
  x: 7.2, y: 1.25, w: 2.3, h: 1.1,
  fontSize: 11, fontFace: "Arial", color: "FF6666",
  valign: "middle", align: "right", italic: true, margin: 0
});

// ── 느림 카드 ──────────────────────────────────
slide.addShape(pres.shapes.RECTANGLE, {
  x: 0.4, y: 2.55, w: 9.2, h: 1.1,
  fill: { color: "0E1420" },
  line: { color: "4488FF", pt: 1.5 }
});
slide.addShape(pres.shapes.RECTANGLE, {
  x: 0.4, y: 2.55, w: 1.1, h: 1.1,
  fill: { color: "4488FF" },
  line: { color: "4488FF" }
});
slide.addText("느림", {
  x: 0.4, y: 2.55, w: 1.1, h: 1.1,
  fontSize: 22, bold: true, color: "FFFFFF",
  fontFace: "Arial", align: "center", valign: "middle", margin: 0
});

slide.addText([
  { text: "평균 오차", options: { color: "8888FF", bold: true } },
  { text: "  >  14 px    ", options: { color: "CCCCFF" } },
  { text: "AND", options: { color: "888888", bold: true } },
  { text: "    진동 비율", options: { color: "8888FF", bold: true } },
  { text: "  <  12%", options: { color: "CCCCFF" } },
], {
  x: 1.65, y: 2.55, w: 5.5, h: 1.1,
  fontSize: 18, fontFace: "Arial", valign: "middle", margin: 0
});

slide.addText("커서가 타겟에 닿지 못하고 뒤처짐", {
  x: 7.2, y: 2.55, w: 2.3, h: 1.1,
  fontSize: 11, fontFace: "Arial", color: "6699FF",
  valign: "middle", align: "right", italic: true, margin: 0
});

// ── 적정 카드 ──────────────────────────────────
slide.addShape(pres.shapes.RECTANGLE, {
  x: 0.4, y: 3.85, w: 9.2, h: 1.0,
  fill: { color: "0C1810" },
  line: { color: "44CC77", pt: 1.5 }
});
slide.addShape(pres.shapes.RECTANGLE, {
  x: 0.4, y: 3.85, w: 1.1, h: 1.0,
  fill: { color: "44CC77" },
  line: { color: "44CC77" }
});
slide.addText("적정", {
  x: 0.4, y: 3.85, w: 1.1, h: 1.0,
  fontSize: 22, bold: true, color: "FFFFFF",
  fontFace: "Arial", align: "center", valign: "middle", margin: 0
});

slide.addText("위 두 조건에 해당하지 않는 경우", {
  x: 1.65, y: 3.85, w: 5.5, h: 1.0,
  fontSize: 17, fontFace: "Arial", color: "AAFFCC", valign: "middle", margin: 0
});

slide.addText("현재 감도가 잘 맞습니다", {
  x: 7.2, y: 3.85, w: 2.3, h: 1.0,
  fontSize: 11, fontFace: "Arial", color: "55DD88",
  valign: "middle", align: "right", italic: true, margin: 0
});

// ── 하단 용어 설명 2개 ──────────────────────────
// 진동 비율
slide.addShape(pres.shapes.RECTANGLE, {
  x: 0.4, y: 5.05, w: 4.4, h: 0.48,
  fill: { color: "1C1C2A" },
  line: { color: "555577", pt: 1 }
});
slide.addText([
  { text: "진동 비율", options: { bold: true, color: "AAAAFF" } },
  { text: "  —  타겟 기준 커서가 방향을 바꾼 횟수 ÷ 전체 프레임 × 100", options: { color: "888899" } },
], {
  x: 0.5, y: 5.05, w: 4.2, h: 0.48,
  fontSize: 10.5, fontFace: "Arial", valign: "middle", margin: 0
});

// 평균 오차
slide.addShape(pres.shapes.RECTANGLE, {
  x: 5.2, y: 5.05, w: 4.4, h: 0.48,
  fill: { color: "1C1C2A" },
  line: { color: "555577", pt: 1 }
});
slide.addText([
  { text: "평균 오차", options: { bold: true, color: "AAAAFF" } },
  { text: "  —  커서와 타겟 사이의 평균 픽셀 거리", options: { color: "888899" } },
], {
  x: 5.3, y: 5.05, w: 4.2, h: 0.48,
  fontSize: 10.5, fontFace: "Arial", valign: "middle", margin: 0
});

pres.writeFile({ fileName: "slide4.pptx" });
console.log("Done: slide4.pptx");
