# IP pack 注入目录

- `oss_ip_pack.py`：开源版（会进入公开仓库，包含可公开分类法；
  不得包含真实素材名、人员姓名、项目标识、投放数据、客户信息或私有判例）
- `ip_pack.py`：**私有自定义 IP 包放这里**（本文件已被 .gitignore，不会提交）

私有部署启用真版：把真版复制为 `ip_pack/ip_pack.py`，取消
`docker-compose.yml` 中 api 服务的挂载注释，然后
`docker compose up -d --force-recreate api`。
