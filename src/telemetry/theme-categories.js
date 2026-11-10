// 主题分类：悬浮窗主题选择器按「主分类」分组，不做多分类重复展示；
// 主面板主题选择器不分组，直接列卡片（ThemePicker.vue 里 mode === 'monitor' 走 flat）。
// 这是 UI 侧的单一真源，theme.json 里只是可选的字符串，Rust 扫描不解释它。
// desc 只是分类语义说明（写文档 / 给主题作者看），设置页按用户要求不展示它：
// 侧栏每项只给「共几个 / 已启用几个」两个数字，右侧标题栏也不铺分类描述。
export const UNCATEGORIZED = 'uncategorized';

export const THEME_CATEGORIES = [
  { id: 'core', label: '核心驾驶', desc: '速度 / 挡位 / 转速，以及和圈速差值组合的 HUD' },
  { id: 'timing', label: '圈速与差值', desc: '本圈 / 最佳 / PB / delta / 扇区' },
  { id: 'inputs', label: '操控输入', desc: '油门 / 刹车 / 转向 / TC / ABS' },
  { id: 'tyres', label: '轮胎与制动', desc: '胎温 / 胎压 / 磨损 / 刹车盘温度' },
  { id: 'fuel', label: '油量与策略', desc: '剩余油量 / 每圈油耗 / 可跑圈数' },
  { id: 'overview', label: '综合 / 全览', desc: '名次 / 圈数 / 旗语 / 天气 / 剩余时间' },
  { id: UNCATEGORIZED, label: '未分类', desc: '没有声明 category 的主题' },
];

const IDS = new Set(THEME_CATEGORIES.map((c) => c.id));

/** 未知 / 缺失 / 非法的 category 一律归「未分类」，不要让主题因此变成坏主题。 */
export function normalizeCategory(value) {
  return IDS.has(value) ? value : UNCATEGORIZED;
}

export function categoryLabel(id) {
  return (THEME_CATEGORIES.find((c) => c.id === id) || THEME_CATEGORIES.at(-1)).label;
}
