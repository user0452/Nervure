taskflow 的 scheduler 在较大 DAG 中，每完成一个节点都会重新全图扫描 runnable tasks，造成明显重复工作。

请优化 runnable task 的维护方式，优先从 dependency bookkeeping / ready queue 方向解决。要求调度依赖顺序、失败传播和 retry 语义保持一致。
完成后运行现有测试。
