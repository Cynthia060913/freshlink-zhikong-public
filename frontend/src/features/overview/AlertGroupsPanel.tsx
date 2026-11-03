import { useQuery } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  type AlertGroup,
  type AlertState,
  alertsApi,
  alertTime,
  stateLabels,
} from "@/features/alerts/api"

const PAGE_SIZE = 6
const DETAIL_SIZE = 10
const kindLabels: Record<string, string> = {
  temperature_high: "温度超上限",
  temperature_low: "温度低于下限",
  humidity_high: "湿度超上限",
  humidity_low: "湿度低于下限",
  data_quality: "数据质量异常",
}

function groupName(group: AlertGroup) {
  const warehouse =
    group.warehouse_name ??
    (group.warehouse_id ? "冷库名称不可用" : "未关联冷库")
  const batch =
    group.batch_code ?? (group.batch_id ? "批次名称不可用" : "未关联批次")
  return `${warehouse} · ${batch}`
}

export default function AlertGroupsPanel() {
  const [page, setPage] = useState(0)
  const [selected, setSelected] = useState<AlertGroup | null>(null)
  const [detailPage, setDetailPage] = useState(0)
  const [state, setState] = useState<AlertState | "">("")
  const [eventId, setEventId] = useState<string | null>(null)
  const [, tick] = useState(0)
  useEffect(() => {
    const timer = window.setInterval(() => tick((n) => n + 1), 1000)
    return () => window.clearInterval(timer)
  }, [])
  const groups = useQuery({
    queryKey: ["alert-groups", page],
    queryFn: () => alertsApi.groups(page * PAGE_SIZE, PAGE_SIZE),
    refetchInterval: 10_000,
    retry: false,
  })
  const details = useQuery({
    queryKey: [
      "alert-group-records",
      selected?.warehouse_id,
      selected?.batch_id,
      detailPage,
      state,
    ],
    queryFn: () =>
      alertsApi.list(detailPage * DETAIL_SIZE, DETAIL_SIZE, {
        ...(selected?.warehouse_id
          ? { warehouse_id: selected.warehouse_id }
          : { unassigned_warehouse: true }),
        ...(selected?.batch_id
          ? { batch_id: selected.batch_id }
          : { unassigned_batch: true }),
        ...(state ? { state } : {}),
      }),
    enabled: selected !== null,
    refetchInterval: 10_000,
    retry: false,
  })
  const lifecycle = useQuery({
    queryKey: ["alert-group-lifecycle", eventId],
    queryFn: () => alertsApi.lifecycle(eventId!),
    enabled: selected !== null && eventId !== null,
    refetchInterval: 10_000,
    retry: false,
  })
  const fresh = (query: { isSuccess: boolean; dataUpdatedAt: number }) =>
    query.isSuccess && Date.now() - query.dataUpdatedAt <= 30_000
  return (
    <Card role="region" aria-label="冷库与批次告警归组" className="min-w-0">
      <CardHeader>
        <CardTitle>异常集中在哪个批次？</CardTitle>
        <p className="text-sm text-muted-foreground">
          按冷库与批次归组现有告警，优先显示未恢复记录较多的分组。数量是历史告警记录数，不是独立事故数或果品损失。
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        {!fresh(groups) ? (
          <p role={groups.isError ? "alert" : "status"}>
            {groups.isPending
              ? "正在读取分组…"
              : "分组读取失败或快照过期，已隐藏数量；不能据此判断没有异常。"}
          </p>
        ) : groups.data?.count === 0 ? (
          <p>
            尚无可归组的告警记录；不代表环境正常。设备离线与故障请在设备管理核查。
          </p>
        ) : (
          <>
            <p className="text-xs text-muted-foreground">
              共 {groups.data?.count} 个分组 · 查询时间{" "}
              {alertTime(groups.data?.queried_at ?? "")} ·
              不包含设备连接事件和恢复事件本身
            </p>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {groups.data?.data.map((group) => (
                <section
                  key={`${group.warehouse_id}/${group.batch_id}`}
                  className="min-w-0 space-y-3 rounded-lg border p-4"
                >
                  <h3 className="break-words font-medium">
                    {groupName(group)}
                  </h3>
                  <p className="text-xs text-muted-foreground">
                    {group.product ?? "品种未记录"} · 总计 {group.total_count}{" "}
                    条
                  </p>
                  <dl className="grid grid-cols-3 gap-2 text-sm">
                    <div>
                      <dt className="text-muted-foreground">待处理</dt>
                      <dd className="text-xl font-semibold">
                        {group.open_count}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">已确认</dt>
                      <dd className="text-xl font-semibold">
                        {group.acknowledged_count}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">已恢复</dt>
                      <dd className="text-xl font-semibold">
                        {group.resolved_count}
                      </dd>
                    </div>
                  </dl>
                  <p className="text-xs text-muted-foreground">
                    最近记录 {alertTime(group.latest_created_at)}
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setSelected(group)
                      setDetailPage(0)
                      setState("")
                      setEventId(null)
                    }}
                  >
                    查看本组记录
                  </Button>
                </section>
              ))}
            </div>
          </>
        )}
        <p className="text-xs text-muted-foreground">
          已确认只表示知悉，仍需跟进；已恢复由机器判定，不代表人工现场复核。分组与明细分别实时查询，刷新间隔内数量可能变化。
        </p>
        <div className="flex flex-wrap items-center justify-end gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void groups.refetch()}
            disabled={groups.isFetching}
          >
            刷新分组
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={page === 0 || groups.isFetching}
            onClick={() => setPage(page - 1)}
          >
            上一组页
          </Button>
          <span className="text-sm">第 {page + 1} 页</span>
          <Button
            variant="outline"
            size="sm"
            disabled={
              !fresh(groups) ||
              !groups.data ||
              (page + 1) * PAGE_SIZE >= groups.data.count ||
              groups.isFetching
            }
            onClick={() => setPage(page + 1)}
          >
            下一组页
          </Button>
        </div>
      </CardContent>
      <Dialog
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) {
            setSelected(null)
            setEventId(null)
          }
        }}
      >
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>
              {selected ? groupName(selected) : "分组记录"}
            </DialogTitle>
            <DialogDescription>
              按本组冷库与批次精确筛选原始告警；此处只读，不确认或恢复告警。
            </DialogDescription>
          </DialogHeader>
          <p className="break-all text-xs text-muted-foreground">
            冷库 ID：{selected?.warehouse_id ?? "未关联"}
            <br />
            批次 ID：{selected?.batch_id ?? "未关联"}
          </p>
          <label className="flex items-center gap-2 text-sm">
            记录状态
            <select
              aria-label="记录状态"
              className="rounded border bg-background p-2"
              value={state}
              onChange={(e) => {
                setState(e.target.value as AlertState | "")
                setDetailPage(0)
                setEventId(null)
              }}
            >
              <option value="">全部</option>
              <option value="open">待处理</option>
              <option value="acknowledged">已确认</option>
              <option value="resolved">已恢复</option>
            </select>
          </label>
          {!fresh(details) ? (
            <p role={details.isError ? "alert" : "status"}>
              {details.isPending
                ? "正在读取原始记录…"
                : "明细查询失败或已过期，不能解释为没有记录。"}
            </p>
          ) : (
            <>
              <p className="text-sm">当前筛选共 {details.data?.count} 条记录</p>
              {details.data?.data.length === 0 && (
                <p>当前筛选无记录，不代表环境正常。</p>
              )}
              <ul className="space-y-3">
                {details.data?.data.map((row) => (
                  <li
                    className="space-y-1 rounded border p-3 text-sm"
                    key={row.alert_event_id}
                  >
                    <p className="font-medium">
                      {kindLabels[row.kind] ?? row.kind} ·{" "}
                      {stateLabels[row.status]}
                    </p>
                    <p>
                      {alertTime(row.sampled_at)} ·{" "}
                      {row.value !== null
                        ? `${row.value} ${row.unit ?? ""}`
                        : "无有效观测值"}
                    </p>
                    <p className="break-all text-xs">
                      来源 ID：{row.source_event_id}
                    </p>
                    {row.related_source_event_id && (
                      <p className="break-all text-xs">
                        关联来源：{row.related_source_event_id}
                      </p>
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setEventId(row.alert_event_id)}
                    >
                      查看状态记录
                    </Button>
                  </li>
                ))}
              </ul>
            </>
          )}
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={detailPage === 0}
              onClick={() => {
                setDetailPage(detailPage - 1)
                setEventId(null)
              }}
            >
              上一记录页
            </Button>
            <span className="text-sm">第 {detailPage + 1} 页</span>
            <Button
              variant="outline"
              size="sm"
              disabled={
                !fresh(details) ||
                !details.data ||
                (detailPage + 1) * DETAIL_SIZE >= details.data.count
              }
              onClick={() => {
                setDetailPage(detailPage + 1)
                setEventId(null)
              }}
            >
              下一记录页
            </Button>
          </div>
          {eventId && (
            <section
              className="space-y-2 rounded border p-3 text-sm"
              aria-label="本组告警状态追溯"
            >
              <h3 className="font-medium">状态追溯</h3>
              <p className="break-all text-xs">告警 ID：{eventId}</p>
              {!fresh(lifecycle) ? (
                <p>状态暂不可用，请稍后重试。</p>
              ) : (
                <>
                  <p>
                    当前：{lifecycle.data && stateLabels[lifecycle.data.status]}
                  </p>
                  {lifecycle.data?.transitions.length === 0 && (
                    <p>暂无状态迁移记录。</p>
                  )}
                  {lifecycle.data?.transitions.map((t) => (
                    <p className="break-all" key={t.id}>
                      {stateLabels[t.from_status]} → {stateLabels[t.to_status]}{" "}
                      · {alertTime(t.created_at)}
                      {t.trigger_source_event_id &&
                        ` · 来源 ${t.trigger_source_event_id}`}
                    </p>
                  ))}
                </>
              )}
            </section>
          )}
        </DialogContent>
      </Dialog>
    </Card>
  )
}
