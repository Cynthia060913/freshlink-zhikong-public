# A38：共享内容容器的真实视口溢出修复

2026-10-22。核心提交 `78f55c9604db162afa7906877d528ca1dd63320c`，
基于控制入口A37 `a57eb902cf67b8e219fe3d90d7f26fba9c691df7`。
仅公共 SidebarInset 的收缩约束和 A 自有浏览器验证器，不改 B/C 页面或接口。

## 问题与最小修复

旧全页截图上侧栏压在历史内容中间，不能仅由截图断言运行布局。新增实际浏览器
几何检查，在真实遥测/原文/CSV场景后滚到原点，记录 viewport/document/侧栏/inset
边界，再要求整页不超出视口、侧栏在左且不覆盖主内容。没有改DOM或注入修复CSS。

旧干净前端树df758b8、2cfa8fc归档构建在新验证器下实际失败：
`work/control-browser-20261022010735-64388/desktop-layout.json`：
viewport=1280，document=1536，sidebar=[0,256]，inset=[256,1536]。
根因为flex内容的默认min-width:auto；w-full内容不能按剩余空间收缩。
只给公共容器补 `min-w-0`，不隐藏整页溢出、不裁掉列、不修改C质量/原文数据。
旧回归缺少视口断言，故此前浏览器/CI绿灯不能证明这个布局正确。

## 修复验证

1. 容器内锁定Bun1.3.12安装436依赖，SDK生成、Biome89文件、Vite/TypeScript
   均通过，前端生成客户端无trackeddiff；无宿主安装、无锁文件变化。
   `work/a01-frontend-20261022-010920-50060.json`，实际bundle
   `work/frontend-bundle-f023697cc4844071905f57fa48a0e11c`。
2. 清洁精确78f55c9，bundle和checkout前端树均
   `9175abde1a7dacb580446827bf3326c3bd25651f`。受控实际浏览器运行：
   `work/control-browser-20261022011357-14340`，dirty_before=false，
   proof_exit=0/cleanup_exit=0。Chromium148.0.7778.96、Playwright1.62.1。
3. 新边界：viewport=document=1280，sidebar=[0,256]，inset=[256,1280]。
   几何JSON SHA256 `68567bfa0abf089f1cf1c1a48bc90f54602e95418f21de84c9f74cef331a48c6`。
4. 同次真实登录/控制→PG→MQTT→设备执行ACK/重复一次/刷新保留/读用户403/降温，
   实际MQTT→A04队列→C临时PG存储→页面/raw→24条固定快照CSV和全部分页ID
   一致、匿名401均通过。浏览器JSON SHA256
   `c125c4baf5ff0bcba054a3104be26166c198baad6efada6ed8f58f30fe80df4b`。
   最终截图已实际打开查看，侧栏在原点、原文及表格仍在。部分窄列换行是保留内容，
   不是截掉数据。两个运行的临时容器/网络均清理0，原应用库未动。

## 材料使用与未验收范围

`docs/submission/media/`保存同次实际控制回执和历史/raw截图，用于B的PPT/设计
候选章节。全部账号、设备/仓库/命令都是隔离合成夹具；不是实际冷库采样。
图片不能证明正式发布、C正式迁移、自动异常恢复、两通知实收或第二机。
图中的候选提示保留，最终应在同版E1成品重录/截图，不能隐去提示后冒充成品。

本轮只验收1280×900桌面展开侧栏，不外推手机/所有折叠状态。
后端业务未改变，沿用A37的1118项/93%证据，不重复称本轮跑了新全套。
PR44精确a35/其合并许可保持不动；本包另须独立审查/适用检查及具体合并许可。
