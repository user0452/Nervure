mini_vcs 在文件较多时执行 status / diff 相关状态计算存在明显重复文件读取和哈希。

请在不改变 status 语义的前提下减少重复 IO / hash。不要使用跨调用永久缓存造成 stale state。
完成后运行现有测试。
