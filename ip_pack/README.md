# IP pack 注入目录

- `oss_ip_pack.py`：开源占位版（会进入公开仓库，功能可跑、无行业方法论）
- `ip_pack.py`：**真版放这里**（本文件已被 .gitignore，不会提交）

私有部署启用真版：把真版复制为 `ip_pack/ip_pack.py`，取消
`docker-compose.yml` 中 api 服务的挂载注释，然后
`docker compose up -d --force-recreate api`。
