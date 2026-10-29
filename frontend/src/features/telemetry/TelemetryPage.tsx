import { useQuery } from "@tanstack/react-query"
import { type FormEvent, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { type TelemetryFilters, type TelemetryItem, telemetryApi, telemetryError } from "./api"
import { isLatestObservationStale } from "./freshness"

type Selection = {
  filters: TelemetryFilters
  snapshotId?: string
  cursor?: string
  revision: number
}

const initialSelection: Selection = { filters: {}, revision: 0 }

function display(value: string | number | null): string {
  return value === null ? "缺失 / 未知" : String(value)
}

function RuleEvidence({ item }: { item: TelemetryItem }) {
  const refs = item.batch_rule_refs
  if (refs && refs.length > 0) {
    return (
      <details>
        <summary>多批次规则（{refs.length}）</summary>
        <ul className="mt-1 space-y-1">
          {refs.map((ref) => (
            <li key={ref.batch_id}>
              批次 {ref.batch_id}：{ref.rule_version}（{ref.state}；快照{" "}
              {ref.rule_snapshot_id}）
            </li>
          ))}
        </ul>
      </details>
    )
  }
  if (item.rule_version) return <span>{item.rule_version}</span>
  if (item.risk_state === "unknown")
    return <span>未评估；规则证据需核查</span>
  return <span>规则证据未记录/需核查</span>
}

const receptionOutcomes: Record<string, string> = {
  accepted_new: "首次保存",
  accepted_duplicate: "重复接收",
  conflict: "消息冲突",
  invalid: "无效数据",
}

const receptionReasons: Record<string, string> = {
  duplicate_message: "与原消息内容相同",
  conflicting_message: "同一消息 ID 的内容改变",
  reception_id_reuse: "接收 ID 被用于不同内容",
}

function utcInput(value: string): string | undefined {
  // datetime-local has no timezone; this form explicitly interprets it as UTC.
  // Browsers may include seconds (or fractions), so do not append another :00.
  return value ? `${value}Z` : undefined
}

export default function TelemetryPage() {
  const [deviceId, setDeviceId] = useState("")
  const [warehouseId, setWarehouseId] = useState("")
  const [sampledFrom, setSampledFrom] = useState("")
  const [sampledTo, setSampledTo] = useState("")
  const [receivedFrom, setReceivedFrom] = useState("")
  const [receivedTo, setReceivedTo] = useState("")
  const [quality, setQuality] = useState("")
  const [selection, setSelection] = useState<Selection>(initialSelection)
  const [csvError, setCsvError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [nowMs, setNowMs] = useState(() => Date.now())
  const hasLatestScope = Boolean(
    selection.filters.device_id || selection.filters.warehouse_id,
  )

  const latest = useQuery({
    queryKey: [
      "telemetry-latest",
      selection.filters.device_id,
      selection.filters.warehouse_id,
    ],
    queryFn: () => telemetryApi.latest(selection.filters),
    enabled: hasLatestScope,
    refetchInterval: 10_000,
    retry: false,
  })
  useEffect(() => {
    if (!hasLatestScope) return
    const timer = window.setInterval(() => setNowMs(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [hasLatestScope])
  const latestIsStale = latest.data?.item
    ? isLatestObservationStale(
        latest.data.checked_at,
        latest.data.item.received_at,
        latest.dataUpdatedAt,
        nowMs,
      )
    : false

  const page = useQuery({
    queryKey: ["telemetry", selection],
    queryFn: () =>
      telemetryApi.page(
        selection.filters,
        selection.snapshotId,
        selection.cursor,
      ),
    retry: false,
  })
  const detail = useQuery({
    queryKey: ["telemetry-detail", selectedId],
    queryFn: () => telemetryApi.detail(selectedId!),
    enabled: selectedId !== null,
    retry: false,
  })

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSelectedId(null)
    setCsvError(null)
    setSelection({
      filters: {
        device_id: deviceId.trim() || undefined,
        warehouse_id: warehouseId.trim() || undefined,
        sampled_from: utcInput(sampledFrom),
        sampled_to: utcInput(sampledTo),
        received_from: utcInput(receivedFrom),
        received_to: utcInput(receivedTo),
        quality_in: quality.trim() ? [quality.trim()] : undefined,
      },
      revision: selection.revision + 1,
    })
  }

  async function download() {
    if (!page.data || downloading) return
    setCsvError(null)
    setDownloading(true)
    try {
      const blob = await telemetryApi.csv(
        selection.filters,
        page.data.snapshot_id,
      )
      const url = URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = "observations.csv"
      document.body.appendChild(link)
      link.click()
      link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 0)
    } catch (error) {
      setCsvError(telemetryError(error))
    } finally {
      setDownloading(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-bold tracking-tight">遥测历史</h1>
        <p className="mt-1 text-muted-foreground">
          同一次查询的分页和 CSV 使用固定快照；刷新筛选会建立新快照。
        </p>
      </header>
      <p className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-4 text-sm">
        本页面是 C
        模块接线候选。无规则、缺测或数据服务失败时风险保持未知；高温原值保留。
      </p>
      <form
        onSubmit={submit}
        className="grid gap-4 rounded-lg border p-5 md:grid-cols-2"
      >
        <div>
          <Label htmlFor="telemetry-device">设备 ID</Label>
          <Input
            id="telemetry-device"
            value={deviceId}
            onChange={(event) => setDeviceId(event.target.value)}
            placeholder="UUID（可选）"
          />
        </div>
        <div>
          <Label htmlFor="telemetry-warehouse">冷库 ID</Label>
          <Input
            id="telemetry-warehouse"
            value={warehouseId}
            onChange={(event) => setWarehouseId(event.target.value)}
            placeholder="UUID（可选）"
          />
        </div>
        <div>
          <Label htmlFor="telemetry-from">采样起始 UTC</Label>
          <Input
            id="telemetry-from"
            type="datetime-local"
            value={sampledFrom}
            onChange={(event) => setSampledFrom(event.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="telemetry-to">采样结束 UTC（不含）</Label>
          <Input
            id="telemetry-to"
            type="datetime-local"
            value={sampledTo}
            onChange={(event) => setSampledTo(event.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="telemetry-received-from">接收起始 UTC</Label>
          <Input
            id="telemetry-received-from"
            type="datetime-local"
            value={receivedFrom}
            onChange={(event) => setReceivedFrom(event.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="telemetry-received-to">接收结束 UTC（不含）</Label>
          <Input
            id="telemetry-received-to"
            type="datetime-local"
            value={receivedTo}
            onChange={(event) => setReceivedTo(event.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="telemetry-quality">质量标记</Label>
          <Input
            id="telemetry-quality"
            value={quality}
            onChange={(event) => setQuality(event.target.value)}
            placeholder="单个标记（可选）"
          />
        </div>
        <div className="flex items-end gap-2">
          <Button type="submit">按筛选新建快照</Button>
          <Button
            type="button"
            variant="outline"
            onClick={() =>
              setSelection((current) => ({
                filters: current.filters,
                revision: current.revision + 1,
              }))
            }
          >
            刷新快照
          </Button>
        </div>
      </form>
      <section className="rounded-lg border p-5">
        <h2 className="font-semibold">最新已存观测</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          仅按已提交的设备或冷库筛选，每 10 秒读取一次；按可信首次接收时间排序。
          历史列表和 CSV 仍使用固定快照；此处风险不代表所有批次已判正常。
        </p>
        {!hasLatestScope ? (
          <p className="mt-3">请先选择设备或冷库；未查询不代表环境正常。</p>
        ) : latest.isPending ? (
          <p role="status" className="mt-3">
            正在读取最新观测…
          </p>
        ) : latest.isError ? (
          <p role="alert" className="mt-3 text-destructive">
            {telemetryError(latest.error)}
          </p>
        ) : latest.data.item === null ? (
          <p className="mt-3">暂无保存观测；当前风险未知。</p>
        ) : (
          <div className="mt-3 space-y-1 text-sm">
            <p>接收 UTC：{latest.data.item.received_at}</p>
            <p>采样 UTC：{display(latest.data.item.sampled_at)}</p>
            <p>温度：{display(latest.data.item.temperature_c)} °C</p>
            <p>质量：{latest.data.item.quality.join(", ") || "无标记"}</p>
            {latestIsStale ? (
              <p role="alert" className="text-destructive">
                最近观测已超过 60
                秒或时间不一致；当前风险未知，不能按旧值判正常。
              </p>
            ) : (
              <p>候选风险：{latest.data.item.risk_state || "unknown"}</p>
            )}
          </div>
        )}
      </section>
      <section className="rounded-lg border p-5">
        <div className="mb-4 flex items-center justify-between gap-3">
          <h2 className="font-semibold">
            观测列表{page.data ? `（固定快照共 ${page.data.total} 条）` : ""}
          </h2>
          <Button
            type="button"
            variant="outline"
            disabled={!page.data || downloading}
            onClick={download}
          >
            {downloading ? "正在导出…" : "导出本次快照 CSV（含批次规则）"}
          </Button>
        </div>
        {csvError && (
          <p role="alert" className="mb-3 text-destructive">
            {csvError}
          </p>
        )}
        {page.isPending ? (
          <p role="status">正在读取观测…</p>
        ) : page.isError ? (
          <p role="alert" className="text-destructive">
            {telemetryError(page.error)}
          </p>
        ) : page.data.items.length === 0 ? (
          <p>该筛选没有观测；空结果不代表环境正常。</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b">
                  <th className="p-2">采样 UTC</th>
                  <th className="p-2">接收 UTC</th>
                  <th className="p-2">设备</th>
                  <th className="p-2">温度 °C</th>
                  <th className="p-2">湿度 %</th>
                  <th className="p-2">质量 / 风险</th>
                  <th className="p-2">规则版本 / 批次映射</th>
                  <th className="p-2">原始证据</th>
                </tr>
              </thead>
              <tbody>
                {page.data.items.map((item) => (
                  <tr key={item.observation_id} className="border-b">
                    <td className="p-2">{display(item.sampled_at)}</td>
                    <td className="p-2">{item.received_at}</td>
                    <td className="p-2">{item.device_id}</td>
                    <td className="p-2">{display(item.temperature_c)}</td>
                    <td className="p-2">{display(item.humidity_percent)}</td>
                    <td className="p-2">
                      {item.quality.join(", ") || "无标记"} /{" "}
                      {item.risk_state || "unknown"}
                    </td>
                    <td className="max-w-72 break-all p-2 text-xs">
                      <RuleEvidence item={item} />
                    </td>
                    <td className="p-2">
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => setSelectedId(item.observation_id)}
                      >
                        查看
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {page.data?.next_cursor && (
          <Button
            type="button"
            variant="outline"
            className="mt-4"
            onClick={() => {
              const currentPage = page.data
              if (!currentPage?.next_cursor) return
              setSelection((current) => ({
                ...current,
                snapshotId: currentPage.snapshot_id,
                cursor: currentPage.next_cursor!,
              }))
            }}
          >
            下一页
          </Button>
        )}
      </section>
      {selectedId && (
        <section className="rounded-lg border p-5">
          <h2 className="mb-3 font-semibold">观测原始证据</h2>
          {detail.isPending ? (
            <p>读取中…</p>
          ) : detail.isError ? (
            <p role="alert" className="text-destructive">
              {telemetryError(detail.error)}
            </p>
          ) : (
            <>
              <p>质量：{detail.data.quality.join(", ") || "无标记"}</p>
              <pre className="mt-3 max-h-64 overflow-auto rounded bg-muted p-3 text-xs">
                {detail.data.raw_text}
              </pre>
              <p className="mt-3 text-sm">
                与此观测关联的接收记录：{detail.data.receptions.length} 次
              </p>
              {detail.data.reception_reuse_conflict_count > 0 && (
                <p role="alert" className="mt-2 text-sm text-destructive">
                  接收 ID 异文复用：{detail.data.reception_reuse_conflict_count}
                  次。原始复用记录需由管理员审计；原观测风险不能代表这些冲突数据。
                </p>
              )}
              {detail.data.receptions.some(
                (attempt) =>
                  attempt.outcome === "conflict" ||
                  attempt.outcome === "invalid",
              ) && (
                <p role="alert" className="mt-2 text-sm text-destructive">
                  存在被拒绝的接收记录；上方风险只对应原观测，不能把冲突数据判为正常。
                </p>
              )}
              <ul className="mt-2 space-y-2 text-sm">
                {detail.data.receptions.map((attempt) => (
                  <li key={attempt.reception_id} className="rounded border p-2">
                    <span>{attempt.received_at}</span>
                    {" · "}
                    <span>
                      {receptionOutcomes[attempt.outcome] ?? "待核查接收结果"}
                    </span>
                    {attempt.reason && (
                      <span>
                        {" · "}
                        {receptionReasons[attempt.reason] ?? "原因待核查"}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}
    </div>
  )
}
