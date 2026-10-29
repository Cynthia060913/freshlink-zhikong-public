import { createFileRoute, Link } from "@tanstack/react-router"

import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/")({
  component: Dashboard,
  head: () => ({
    meta: [
      {
        title: "项目首页 - 鲜链智控",
      },
    ],
  }),
})

function Dashboard() {
  const { user: currentUser } = useAuth()

  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <h1 className="text-3xl font-semibold">欢迎使用鲜链智控</h1>
        <p className="text-muted-foreground">
          苹果冷库环境监测、设备控制与异常处置。
        </p>
        <p className="text-sm text-muted-foreground">
          当前账户：{currentUser?.full_name || currentUser?.email}
        </p>
      </header>
      <nav aria-label="项目功能" className="flex flex-wrap gap-6">
        <Link
          to="/devices"
          className="font-medium text-primary underline-offset-4 hover:underline"
        >
          设备管理与控制
        </Link>
        <Link
          to="/telemetry"
          className="font-medium text-primary underline-offset-4 hover:underline"
        >
          遥测历史与导出
        </Link>
        <Link
          to="/alerts"
          className="font-medium text-primary underline-offset-4 hover:underline"
        >
          告警处置与追溯
        </Link>
      </nav>
      <section className="max-w-3xl space-y-3 border-t pt-6">
        <h2 className="text-lg font-semibold">演示与数据说明</h2>
        <p className="text-sm text-muted-foreground">
          设备和采样数据由软件仿真生成。设备管理可查看在线状态与控制执行回执；
          遥测历史保留原始观测和质量标记，同次查询的页面与 CSV 使用固定快照；
          告警处置保留确认、恢复和来源记录。
        </p>
        <p className="text-sm text-muted-foreground">
          当前采用版本化阈值规则，未启用智能算法。空数据、缺测或查询失败不能解释为环境正常。
        </p>
      </section>
    </div>
  )
}
