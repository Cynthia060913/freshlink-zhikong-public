import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { Activity, ArrowRight, Bell, Database, Thermometer } from "lucide-react"
import { type ReactNode, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { alertsApi, alertTime } from "@/features/alerts/api"
import { devicesApi, deviceTime } from "@/features/devices/api"
import { connectionLabel } from "@/features/devices/connection"
import DeviceForecastPanel from "@/features/devices/DeviceForecastPanel"
import { forecastApi } from "@/features/devices/forecast-api"
import { telemetryApi } from "@/features/telemetry/api"
import { isLatestObservationStale } from "@/features/telemetry/freshness"

const DEVICE_LIMIT = 100
const SNAPSHOT_AGE_MS = 30_000
const alertKinds: Record<string, string> = {
  temperature_high: "温度超限",
  temperature_low: "温度低于下限",
  humidity_high: "湿度超上限",
  humidity_low: "湿度低于下限",
  threshold_exceeded: "数据超限",
  device_offline: "设备离线",
  data_quality: "数据质量异常",
}

function SummaryCard({
  title,
  icon,
  value,
  children,
}: {
  title: string
  icon: ReactNode
  value: ReactNode
  children: ReactNode
}) {
  return (
    <Card className="gap-3" role="region" aria-label={title}>
      <CardHeader className="flex flex-row items-center justify-between gap-3">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {title}
        </CardTitle>
        <span className="text-muted-foreground" aria-hidden="true">
          {icon}
        </span>
      </CardHeader>
      <CardContent>
        <div className="text-3xl font-semibold tabular-nums">{value}</div>
        <div className="mt-2 text-xs leading-relaxed text-muted-foreground">
          {children}
        </div>
      </CardContent>
    </Card>
  )
}

export default function OverviewPage() {
  const [selectedId, setSelectedId] = useState("")
  const [now, setNow] = useState(Date.now)
  const devices = useQuery({
    queryKey: ["overview-devices"],
    queryFn: () => devicesApi.list(0, DEVICE_LIMIT),
    refetchInterval: 10_000,
    retry: false,
  })
  const openAlerts = useQuery({
    queryKey: ["overview-alerts", "open"],
    queryFn: () => alertsApi.list(0, 4, { state: "open" }),
    refetchInterval: 10_000,
    retry: false,
  })
  const capability = useQuery({
    queryKey: ["forecast-preview-capability"],
    queryFn: forecastApi.capability,
    refetchInterval: 30_000,
    retry: false,
  })
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [])

  // Counts describe the returned server snapshot, never inferred liveness.
  // A bounded list or failed/aged request must not look like an all-clear.
  const devicesReady =
    devices.isSuccess && now - devices.dataUpdatedAt <= SNAPSHOT_AGE_MS
  const alertsReady =
    openAlerts.isSuccess && now - openAlerts.dataUpdatedAt <= SNAPSHOT_AGE_MS
  const capabilityReady =
    capability.isSuccess && now - capability.dataUpdatedAt <= 60_000
  const rows = devicesReady ? devices.data.data : []
  const sensors = rows.filter((device) => device.kind === "temperature_sensor")
  const selected =
    sensors.find((device) => device.id === selectedId) ?? sensors[0]
  const online = rows.filter(
    (device) => connectionLabel(device) === "在线",
  ).length
  const offline = rows.filter(
    (device) => connectionLabel(device) === "离线",
  ).length
  const partial = devicesReady && devices.data.count > rows.length
  const latest = useQuery({
    queryKey: [
      "telemetry-latest",
      selected?.id,
      selected?.warehouse_id ?? undefined,
    ],
    queryFn: () =>
      telemetryApi.latest({
        device_id: selected!.id,
        warehouse_id: selected!.warehouse_id ?? undefined,
      }),
    enabled: Boolean(selected),
    refetchInterval: 10_000,
    retry: false,
  })
  const latestItem = selected && latest.isSuccess ? latest.data.item : null
  const wrongScope =
    latestItem &&
    selected &&
    (latestItem.device_id !== selected.id ||
      (selected.warehouse_id !== null &&
        latestItem.warehouse_id !== selected.warehouse_id))
  const item = wrongScope ? null : latestItem
  const stale =
    item &&
    (!item.sampled_at ||
      isLatestObservationStale(
        latest.data!.checked_at,
        item.received_at,
        latest.dataUpdatedAt,
        now,
      ) ||
      isLatestObservationStale(
        latest.data!.checked_at,
        item.sampled_at,
        latest.dataUpdatedAt,
        now,
      ))
  const qualityOk = item?.quality.length === 1 && item.quality[0] === "ok"
  const usable =
    item &&
    !stale &&
    qualityOk &&
    item.temperature_c !== null &&
    Number.isFinite(item.temperature_c)
  const forecastLabel = !capabilityReady
    ? capability.isPending
      ? "读取中"
      : "状态未知"
    : capability.data.enabled
      ? "实验已开启"
      : "当前关闭"

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-2">
          <p className="text-sm font-medium text-primary">
            冷库巡检 · 业务总览
          </p>
          <h1 className="text-3xl font-semibold">欢迎使用鲜链智控</h1>
          <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
            先核连接与采样，再处理告警。温度预测用于巡检参考，设备控制以执行回执为准。
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => {
            void devices.refetch()
            void openAlerts.refetch()
            void capability.refetch()
            if (selected) void latest.refetch()
          }}
          disabled={
            devices.isFetching || openAlerts.isFetching || latest.isFetching
          }
        >
          刷新总览
        </Button>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          title="已注册设备"
          icon={<Database className="size-4" />}
          value={devicesReady ? devices.data.count : "—"}
        >
          {devicesReady
            ? `当前列表 ${rows.length} 台${partial ? "，仅展示前100台" : "，覆盖全部设备"}`
            : devices.isPending
              ? "正在读取设备"
              : "设备读取失败或快照过期"}
        </SummaryCard>
        <SummaryCard
          title="设备连接"
          icon={<Activity className="size-4" />}
          value={devicesReady ? `${online} 在线` : "—"}
        >
          {devicesReady
            ? `${offline} 离线 · ${rows.length - online - offline} 未知${partial ? "（当前列表范围）" : ""}`
            : "无法判断连接状态，不能按零台在线处理"}
        </SummaryCard>
        <SummaryCard
          title="待处理告警"
          icon={<Bell className="size-4" />}
          value={alertsReady ? openAlerts.data.count : "—"}
        >
          {alertsReady
            ? "全库待确认记录；确认不代表异常已恢复"
            : openAlerts.isPending
              ? "正在读取告警"
              : "告警读取失败或快照过期，不代表没有告警"}
        </SummaryCard>
        <SummaryCard
          title="60秒温度参考"
          icon={<Thermometer className="size-4" />}
          value={<span className="text-xl">{forecastLabel}</span>}
        >
          {capabilityReady && capability.data.enabled
            ? "只读单点预测；不触发告警、通知或调控"
            : capabilityReady
              ? "阈值告警与人工控制继续运行"
              : "尚不能确认预测是否开启"}
        </SummaryCard>
      </div>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Card className="min-w-0" role="region" aria-label="温度巡检">
          <CardHeader>
            <CardTitle>温度巡检</CardTitle>
            <p className="text-sm text-muted-foreground">
              每次只查看一个测点，质量、时效与规则结论分别核对。
            </p>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <label htmlFor="overview-device" className="text-sm font-medium">
                温度测点
              </label>
              <select
                id="overview-device"
                className="h-10 w-full rounded-md border bg-background px-3 text-sm"
                value={selected?.id ?? ""}
                onChange={(event) => setSelectedId(event.target.value)}
                disabled={!devicesReady || sensors.length === 0}
              >
                {sensors.length === 0 && (
                  <option value="">
                    {devicesReady ? "当前列表没有温度测点" : "设备列表不可用"}
                  </option>
                )}
                {sensors.map((device) => (
                  <option key={device.id} value={device.id}>
                    {device.name} · {device.code}
                  </option>
                ))}
              </select>
            </div>
            {!devicesReady ? (
              <p role="status" className="text-sm">
                设备信息未就绪，暂不显示采样数值。
              </p>
            ) : !selected ? (
              <p role="status" className="text-sm">
                请先在设备管理中注册并绑定温度测点。
              </p>
            ) : latest.isPending ? (
              <p role="status">正在读取该测点的最新观测…</p>
            ) : latest.isError ? (
              <p role="alert" className="text-sm text-destructive">
                采样读取失败，已隐藏上次数值。请检查服务后刷新。
              </p>
            ) : wrongScope ? (
              <p role="alert" className="text-sm text-destructive">
                观测归属与当前测点不一致，已隐藏数值。请刷新核查。
              </p>
            ) : !item ? (
              <p role="status" className="text-sm">
                该测点尚无观测，不能据此判定环境正常。
              </p>
            ) : (
              <>
                <div className="flex flex-wrap items-end justify-between gap-3 rounded-lg bg-muted/50 p-4">
                  <div>
                    <p className="text-xs text-muted-foreground">
                      最新可用温度
                    </p>
                    <p
                      className="mt-1 text-4xl font-semibold tabular-nums"
                      data-testid="overview-temperature"
                    >
                      {usable ? `${item.temperature_c!.toFixed(2)} °C` : "—"}
                    </p>
                  </div>
                  <div className="space-y-1 text-sm">
                    <p>
                      {stale
                        ? "观测已过期，等待新采样"
                        : qualityOk
                          ? "质量标记 ok"
                          : "质量待核查，暂不提供当前温度"}
                    </p>
                    <p className="text-muted-foreground">
                      设备：{connectionLabel(selected)}
                    </p>
                  </div>
                </div>
                <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-xs">
                  <dt className="text-muted-foreground">采样时间</dt>
                  <dd>{deviceTime(item.sampled_at)}</dd>
                  <dt className="text-muted-foreground">接收时间</dt>
                  <dd>{deviceTime(item.received_at)}</dd>
                  <dt className="text-muted-foreground">质量标记</dt>
                  <dd className="break-all">
                    {item.quality.join("、") || "未记录"}
                  </dd>
                  <dt className="text-muted-foreground">冷库标识</dt>
                  <dd className="break-all">{item.warehouse_id}</dd>
                  <dt className="text-muted-foreground">观测标识</dt>
                  <dd className="break-all">{item.observation_id}</dd>
                </dl>
                <p className="text-xs text-muted-foreground">
                  温度与连接状态不等于批次正常；批次规则、历史暴露和原文请到遥测历史核查。
                </p>
              </>
            )}
            {selected && capabilityReady && capability.data.enabled && (
              <DeviceForecastPanel key={selected.id} device={selected} />
            )}
            <Link
              to="/telemetry"
              className="inline-flex items-center gap-2 text-sm font-medium text-primary hover:underline"
            >
              查询历史与同快照 CSV{" "}
              <ArrowRight className="size-4" aria-hidden="true" />
            </Link>
          </CardContent>
        </Card>

        <Card className="min-w-0" role="region" aria-label="待处理记录">
          <CardHeader>
            <CardTitle>待处理记录</CardTitle>
            <p className="text-sm text-muted-foreground">
              全库范围，最近四条待确认告警。
            </p>
          </CardHeader>
          <CardContent className="space-y-4">
            {!alertsReady ? (
              <p
                role={openAlerts.isError ? "alert" : "status"}
                className="text-sm"
              >
                {openAlerts.isPending
                  ? "正在读取…"
                  : "暂时无法读取告警；请勿据此判断没有异常。"}
              </p>
            ) : openAlerts.data.count === 0 ? (
              <p role="status" className="text-sm">
                当前没有待确认告警。已确认但尚未恢复的记录仍需在告警页跟进。
              </p>
            ) : (
              <ul className="divide-y">
                {openAlerts.data.data.map((alert) => (
                  <li
                    key={alert.alert_event_id}
                    className="space-y-1 py-3 first:pt-0"
                  >
                    <p className="text-sm font-medium">
                      {alertKinds[alert.kind] ?? alert.kind}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {alertTime(alert.sampled_at)}
                      {alert.value !== null
                        ? ` · ${alert.value} ${alert.unit ?? ""}`
                        : ""}
                    </p>
                    <p className="break-all text-xs text-muted-foreground">
                      来源 {alert.source_event_id}
                    </p>
                  </li>
                ))}
              </ul>
            )}
            <Link
              to="/alerts"
              className="inline-flex items-center gap-2 text-sm font-medium text-primary hover:underline"
            >
              查看处置与恢复追溯{" "}
              <ArrowRight className="size-4" aria-hidden="true" />
            </Link>
          </CardContent>
        </Card>
      </div>

      <nav aria-label="项目功能" className="grid gap-3 md:grid-cols-3">
        {[
          {
            to: "/devices" as const,
            title: "设备管理与控制",
            detail: "注册、连接事件、远程命令与执行 ACK",
          },
          {
            to: "/telemetry" as const,
            title: "遥测历史与导出",
            detail: "原始观测、质量标记、批次规则与快照 CSV",
          },
          {
            to: "/alerts" as const,
            title: "告警处置与追溯",
            detail: "确认、恢复及关联来源；通知记录单独核查",
          },
        ].map((entry) => (
          <Link
            key={entry.to}
            to={entry.to}
            className="rounded-lg border p-4 transition-colors hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-primary"
          >
            <p className="font-medium">{entry.title}</p>
            <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
              {entry.detail}
            </p>
          </Link>
        ))}
      </nav>
      <footer className="space-y-1 border-t pt-4 text-xs leading-relaxed text-muted-foreground">
        <p>
          演示设备和数据由软件仿真生成。基础业务采用版本化阈值规则；预测是独立、默认关闭的实验性数值参考，尚未证明能改善业务提前预警。
        </p>
        <p>
          总览每10秒刷新设备、采样与告警，超过30秒的设备／告警快照撤下数值；空数据、缺测、查询失败均不能解释为环境正常。
        </p>
      </footer>
    </div>
  )
}
