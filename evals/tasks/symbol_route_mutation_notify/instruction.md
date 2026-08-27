route_lab 的路线缓存存在失效问题：修改部分道路权重后，某些路线查询仍可能返回修改前的结果。

请定位并修复 `Graph.update_weight` 的 mutation 通知语义，使权重更新能够触发现有的、按 edge 精确失效的缓存链路。开始编辑前先定位这个符号的真实定义，再运行现有测试。

不能删除缓存功能，也不能在每次任意 Graph mutation 后无脑清空全部缓存；应保持有针对性的 invalidation。
