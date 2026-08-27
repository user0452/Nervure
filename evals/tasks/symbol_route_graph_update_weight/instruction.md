route_lab 修改边权重后，依赖该边的已缓存路线有时仍返回修改前的旧结果，而其他 Graph mutation 的失效通知行为是正常的。

请检查并修复 `Graph.update_weight` 的 mutation 通知语义，使权重更新能够触发现有的、按 edge 精确失效的缓存链路。

不要删除缓存功能，也不要在每次 Graph mutation 时粗暴清空全部缓存。完成后运行现有测试。
