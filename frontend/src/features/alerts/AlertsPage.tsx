import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import useAuth from "@/hooks/useAuth"
import {
  type AlertQuery,
  type AlertRow,
  type AlertState,
  alertError,
  alertsApi,
  alertTime,
  stateLabels,
} from "./api"

const pageSize = 20
const kindLabels: Record<string, string> = {
  temperature_high: "温度超上限",
  temperature_low: "温度低于下限",
  humidity_high: "湿度超上限",
  humidity_low: "湿度低于下限",
  data_quality: "数据质量",
}
const severityLabels: Record<string, string> = {
  info: "提示",
  warning: "警告",
  critical: "严重",
}

function kindLabel(kind: string): string {
  return kindLabels[kind] ?? kind
}

function valueText(row: AlertRow): string {
  if (row.value === null) return "无观测值"
  if (!Number.isFinite(row.value)) return "观测值无效"
  return `${row.value}${row.unit ?? ""}`
}

export default function AlertsPage() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(0)
  const [stateFilter, setStateFilter] = useState<AlertState | "">("")
  const [severityFilter, setSeverityFilter] = useState("")
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const query: AlertQuery = {
    ...(stateFilter ? { state: stateFilter } : {}),
    ...(severityFilter ? { severity: severityFilter } : {}),
  }
  const list = useQuery({
    queryKey: ["alerts", page, stateFilter, severityFilter],
    queryFn: () => alertsApi.list(page * pageSize, pageSize, query),
    refetchInterval: 10000,
  })
  const lifecycle = useQuery({
    queryKey: ["alert-lifecycle", selectedId],
    queryFn: () => alertsApi.lifecycle(selectedId!),
    enabled: selectedId !== null,
    refetchInterval: 10000,
  })
  const acknowledge = useMutation({
    mutationFn: alertsApi.acknowledge,
    onSuccess: (body) => {
      setNotice(
        body.transition_created
          ? "已确认该告警；确认表示值班人员已知悉，不代表故障已恢复。"
          : "该告警此前已确认，未重复写入迁移记录。",
      )
      queryClient.invalidateQueries({ queryKey: ["alerts"] })
      queryClient.invalidateQueries({ queryKey: ["alert-lifecycle"] })
    },
  })

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-bold tracking-tight">告警处置</h1>
        <p className="mt-1 text-muted-foreground">
          鲜链智控 · 异常告警状态与确认记录
        </p>
      </header>
      <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-4 text-sm">
        “已恢复”由机器根据恢复事件判定，不等于现场人工复核完成；“已确认”只
        表示值班人员已知悉。列表加载失败不代表没有告警；确认操作仅限管理员，
        普通账号为只读。
      </div>
      <section className="rounded-lg border p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-semibold">
            告警列表{list.data ? `（共 ${list.data.count} 条）` : ""}
          </h2>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm">
              状态
              <select
                value={stateFilter}
                onChange={(e) => {
                  setStateFilter(e.target.value as AlertState | "")
                  setPage(0)
                }}
                className="h-9 rounded-md border bg-background px-2 text-sm"
              >
                <option value="">全部</option>
                <option value="open">待处理</option>
                <option value="acknowledged">已确认</option>
                <option value="resolved">已恢复</option>
              </select>
            </label>
            <label className="flex items-center gap-2 text-sm">
              级别
              <select
                value={severityFilter}
                onChange={(e) => {
                  setSeverityFilter(e.target.value)
                  setPage(0)
                }}
                className="h-9 rounded-md border bg-background px-2 text-sm"
              >
                <option value="">全部</option>
                <option value="info">提示</option>
                <option value="warning">警告</option>
                <option value="critical">严重</option>
              </select>
            </label>
            <Button
              variant="outline"
              onClick={() => list.refetch()}
              disabled={list.isFetching}
            >
              {list.isFetching ? "加载中…" : "刷新"}
            </Button>
          </div>
        </div>
        {notice && (
          <p role="status" className="mb-3 text-sm text-emerald-500">
            {notice}
          </p>
        )}
        {list.isPending ? (
          <p role="status">正在加载告警…</p>
        ) : list.isError ? (
          <div role="alert" className="text-destructive">
            {alertError(list.error)}
            <p className="mt-2 text-xs">
              请使用“刷新”重试；查询失败不解释为没有告警。
            </p>
          </div>
        ) : list.data.data.length === 0 ? (
          <p className="py-6 text-center text-muted-foreground">
            当前筛选条件下没有告警记录；不代表仓储温湿度正常。
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b">
                  <th className="p-3">类型 / 级别</th>
                  <th className="p-3">观测值</th>
                  <th className="p-3">采样时间</th>
                  <th className="p-3">状态</th>
                  <th className="p-3">来源 ID</th>
                  <th className="p-3">操作</th>
                </tr>
              </thead>
              <tbody>
                {list.data.data.map((row) => (
                  <tr
                    key={row.alert_event_id}
                    className="border-b last:border-0"
                  >
                    <td className="p-3">
                      <div className="font-medium">{kindLabel(row.kind)}</div>
                      <div className="text-xs text-muted-foreground">
                        {severityLabels[row.severity ?? ""] ??
                          row.severity ??
                          "未分级"}
                      </div>
                    </td>
                    <td className="p-3">{valueText(row)}</td>
                    <td className="p-3">{alertTime(row.sampled_at)}</td>
                    <td className="p-3">
                      <span className="rounded bg-muted px-2 py-1">
                        {stateLabels[row.status]}
                      </span>
                    </td>
                    <td className="max-w-48 break-all p-3 text-xs">
                      {row.source_event_id}
                      {row.related_source_event_id && (
                        <div className="mt-1 break-all text-muted-foreground">
                          关联：{row.related_source_event_id}
                        </div>
                      )}
                    </td>
                    <td className="p-3">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          setNotice(null)
                          setSelectedId(row.alert_event_id)
                        }}
                      >
                        查看 / 处置
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="mt-4 flex items-center justify-end gap-3">
          <Button
            variant="outline"
            size="sm"
            disabled={page === 0 || list.isFetching}
            onClick={() => setPage((p) => p - 1)}
          >
            上一页
          </Button>
          <span className="text-sm">第 {page + 1} 页</span>
          <Button
            variant="outline"
            size="sm"
            disabled={
              !list.data ||
              list.isFetching ||
              (page + 1) * pageSize >= list.data.count
            }
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </Button>
        </div>
      </section>
      <Dialog
        open={selectedId !== null}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedId(null)
            setNotice(null)
            acknowledge.reset()
          }
        }}
      >
        <DialogContent className="max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>告警处置</DialogTitle>
            <DialogDescription>
              时间使用北京时间；状态迁移记录只追加、不删除。
            </DialogDescription>
          </DialogHeader>
          {lifecycle.isPending ? (
            <p>正在加载告警状态…</p>
          ) : lifecycle.isError ? (
            <div role="alert">
              {alertError(lifecycle.error)}
              <Button variant="outline" onClick={() => lifecycle.refetch()}>
                重试
              </Button>
            </div>
          ) : (
            lifecycle.data && (
              <>
                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-3 text-sm">
                  <dt>当前状态</dt>
                  <dd>{stateLabels[lifecycle.data.status]}</dd>
                  <dt>告警事件 ID</dt>
                  <dd className="break-all">{lifecycle.data.alert_event_id}</dd>
                </dl>
                <section className="border-t pt-4" aria-label="状态迁移记录">
                  <h3 className="font-medium">状态迁移记录</h3>
                  {lifecycle.data.transitions.length === 0 ? (
                    <p className="mt-2 text-sm text-muted-foreground">
                      暂无迁移记录；告警仍为“待处理”。
                    </p>
                  ) : (
                    <ol className="mt-2 space-y-3 text-sm">
                      {lifecycle.data.transitions.map((t) => (
                        <li key={t.id} className="rounded border p-3">
                          <div className="font-medium">
                            {stateLabels[t.from_status]} →{" "}
                            {stateLabels[t.to_status]}
                          </div>
                          <div>{alertTime(t.created_at)}</div>
                          {t.trigger_source_event_id && (
                            <div className="break-all text-xs text-muted-foreground">
                              触发来源：{t.trigger_source_event_id}
                            </div>
                          )}
                        </li>
                      ))}
                    </ol>
                  )}
                </section>
                {user?.is_superuser ? (
                  <section className="border-t pt-4">
                    <Button
                      onClick={() => {
                        setNotice(null)
                        acknowledge.mutate(selectedId!)
                      }}
                      disabled={
                        acknowledge.isPending ||
                        lifecycle.data.status !== "open"
                      }
                    >
                      {acknowledge.isPending ? "正在确认…" : "确认告警"}
                    </Button>
                    <p className="mt-2 text-xs text-muted-foreground">
                      仅“待处理”可确认；已恢复的告警不能回退确认。
                    </p>
                    {acknowledge.isError && (
                      <p role="alert" className="mt-2 text-sm text-destructive">
                        {alertError(acknowledge.error)}
                      </p>
                    )}
                  </section>
                ) : (
                  <p className="border-t pt-4 text-sm text-muted-foreground">
                    当前为只读访问；确认操作仅限管理员。
                  </p>
                )}
              </>
            )
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
