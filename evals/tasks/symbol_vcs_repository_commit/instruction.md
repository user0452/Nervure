mini_vcs 的历史提交在某些情况下没有形成真正不可变的快照：创建 commit 后继续修改 staging，可能反向改变已经创建的旧 commit 内容。

请修复 `Repository.commit` 的快照语义，使每个 commit 保存创建时的 staged tree，而不是继续共享之后会被修改的可变映射。

不要通过禁止后续 staging、修改 checkout 行为或每次读取时临时掩盖问题。完成后运行现有测试。
