mini_vcs 在文件重命名后生成的 status 结果存在异常：一个纯重命名会被拆成 deleted + untracked。

请自行定位状态计算流程并修复，使内容未变化的重命名能被正确识别，同时保持 modified / deleted / untracked 的正常语义。
完成后运行现有测试。
