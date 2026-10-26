// ============================================================================
// 弯心识别入口。
//
// 弯的「位置」和「T 编号」由赛道几何（fastlane 几何）纯本地计算：对所有赛道
// 通用，不需要手工名表，也不需要联网——用户装了对应赛道就有几何数据可读。
//
// 弯的「真名」（Eerste / Lesmo / Parabolica…）属于人类命名约定，几何数据里
// 并不存在，任何算法都无法从赛道形状算出来。因此本应用只显示 T1、T2… 编号，
// 不再维护任何赛道名表。
// ============================================================================

import { detectApexes } from './apex.js';

/**
 * 生成某条赛道的弯心列表（仅 T 编号）。
 * trackId 仅为兼容旧调用方保留，识别已不依赖赛道名。
 */
export function apexesForTrack(trackId, geometry) {
  if (!geometry?.points?.length) return [];
  return detectApexes(geometry.points);
}
