import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { type Device, deviceTime } from "./api"
import {
  type CommandInput,
  commandLabels,
  controlApi,
  controlError,
  isTerminal,
} from "./control-api"

const uuidPattern = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i

function createCommandId(): string {
  if (typeof globalThis.crypto?.randomUUID === "function")
    return globalThis.crypto.randomUUID()
  // randomUUID is secure-context-only, but getRandomValues also works on the
  // isolated HTTP CI origin. Format CSPRNG bytes as UUIDv4; never use Math.random.
  if (typeof globalThis.crypto?.getRandomValues !== "function")
    throw new Error("Secure random source unavailable")
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("")
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

// This is a per-tab, per-actor/device pending intent, not a token or authority.
// Keep the exact ID/body across a closed dialog or reload after an uncertain POST.
function restoreIntent(key: string): CommandInput | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) ?? "null")
    if (
      value &&
      typeof value.command_id === "string" &&
      uuidPattern.test(value.command_id) &&
      value.action === "set_cooling" &&
      typeof value.enabled === "boolean" &&
      Number.isInteger(value.lifetime_seconds) &&
      value.lifetime_seconds >= 1 &&
      value.lifetime_seconds <= 300 &&
      value.event_id === null
    )
      return {
        command_id: value.command_id,
        action: "set_cooling",
        enabled: value.enabled,
        lifetime_seconds: value.lifetime_seconds,
        event_id: null,
      }
  } catch {
    // Unavailable/malformed local storage is never permission to send a command.
  }
  return null
}

export default function DeviceControlPanel({
  device,
  actorId,
  canControl,
}: {
  device: Device
  actorId: string
  canControl: boolean
}) {
  const queryClient = useQueryClient()
  const storageKey = `freshlink-control-intent:${actorId}:${device.id}`
  const [pending, setPending] = useState(() => restoreIntent(storageKey))
  const [selectedId, setSelectedId] = useState(pending?.command_id ?? null)
  const [enabled, setEnabled] = useState(pending?.enabled ?? true)
  const [lifetime, setLifetime] = useState(pending?.lifetime_seconds ?? 60)
  const [confirming, setConfirming] = useState(false)
  const [storageError, setStorageError] = useState(false)
  const [idError, setIdError] = useState(false)
  const listKey = ["device-commands", actorId, device.id]
  const history = useQuery({
    queryKey: listKey,
    queryFn: () => controlApi.list(device.id),
    retry: false,
    refetchInterval: 5000,
  })
  const detailKey = ["device-command", actorId, device.id, selectedId]
  const detail = useQuery({
    queryKey: detailKey,
    queryFn: () => controlApi.detail(device.id, selectedId!),
    enabled: selectedId !== null,
    retry: false,
    refetchInterval: 3000,
  })
  const submission = useMutation({
    mutationFn: (body: CommandInput) => controlApi.create(device.id, body),
    // A POST is never automatically retried with a new ID.
    retry: false,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: listKey })
      queryClient.invalidateQueries({ queryKey: detailKey })
    },
  })

  useEffect(() => {
    if (
      pending &&
      detail.isSuccess &&
      detail.data.command_id === pending.command_id &&
      isTerminal(detail.data.status)
    ) {
      try {
        sessionStorage.removeItem(storageKey)
      } catch {
        // A retained old ID is safe: the server still deduplicates it.
      }
      setPending(null)
      submission.reset()
      queryClient.invalidateQueries({
        queryKey: ["device-commands", actorId, device.id],
      })
    }
  }, [
    pending,
    detail.isSuccess,
    detail.data,
    storageKey,
    queryClient,
    actorId,
    device.id,
    submission.reset,
  ])

  const active = history.data?.data.some((item) => !isTerminal(item.status))
  const ready =
    canControl &&
    device.kind === "cooling_actuator" &&
    device.warehouse_id !== null &&
    device.connectivity === "online" &&
    history.isSuccess &&
    !active
  const actionText = enabled ? "开启制冷" : "关闭制冷"

  function sendNewCommand() {
    if (!ready || pending || submission.isPending) return
    setStorageError(false)
    setIdError(false)
    let commandId: string
    try {
      commandId = createCommandId()
    } catch {
      setIdError(true)
      return
    }
    const body: CommandInput = {
      command_id: commandId,
      action: "set_cooling",
      enabled,
      lifetime_seconds: lifetime,
      event_id: null,
    }
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(body))
    } catch {
      setStorageError(true)
      return // Fail closed before POST if an uncertain result cannot be retained.
    }
    setPending(body)
    setSelectedId(body.command_id)
    setConfirming(false)
    submission.mutate(body)
  }

  return (
    <section className="space-y-4 border-t pt-4" aria-label="远程制冷控制">
      <h3 className="font-medium">远程制冷控制</h3>
      <p className="text-xs text-muted-foreground">
        仅控制软件仿真执行器。提交、排队和 MQTT
        接受都不等于设备执行；以服务端保存的设备回执为准。
        关闭本窗口不会取消已经提交的命令。
      </p>
      {!canControl ? (
        <p className="text-sm">当前为只读访问；只有管理员可下发控制。</p>
      ) : device.kind !== "cooling_actuator" ? (
        <p className="text-sm">温度测点不接受制冷控制命令。</p>
      ) : (
        <div className="space-y-3 rounded border p-3">
          {!device.warehouse_id && <p>设备尚未绑定冷库，不能控制。</p>}
          {device.connectivity !== "online" && (
            <p>设备未在线，不能下发新命令；仍可查询历史回执。</p>
          )}
          {active && <p>已有未结束命令，请先查询该命令的回执。</p>}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label htmlFor="control-action">控制动作</Label>
              <select
                id="control-action"
                value={enabled ? "on" : "off"}
                disabled={!!pending || submission.isPending || confirming}
                onChange={(event) => setEnabled(event.target.value === "on")}
                className="h-9 w-full rounded-md border bg-background px-2 text-sm"
              >
                <option value="on">开启制冷</option>
                <option value="off">关闭制冷</option>
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="control-lifetime">命令有效期</Label>
              <select
                id="control-lifetime"
                value={lifetime}
                disabled={!!pending || submission.isPending || confirming}
                onChange={(event) => setLifetime(Number(event.target.value))}
                className="h-9 w-full rounded-md border bg-background px-2 text-sm"
              >
                {[30, 60, 120].map((seconds) => (
                  <option key={seconds} value={seconds}>
                    {seconds} 秒
                  </option>
                ))}
              </select>
            </div>
          </div>
          {!pending && !confirming && (
            <Button
              disabled={!ready || submission.isPending}
              onClick={() => setConfirming(true)}
            >
              准备下发
            </Button>
          )}
          {!pending && confirming && (
            <fieldset
              aria-label="确认控制命令"
              className="space-y-3 rounded border border-amber-500 p-3"
            >
              <p>
                请确认：对 {device.name}（{device.code}）{actionText}，有效期{" "}
                {lifetime} 秒。
              </p>
              <p className="break-all text-xs">
                目标设备：{device.id}；冷库：{device.warehouse_id}
              </p>
              <Button
                disabled={!ready || submission.isPending}
                onClick={sendNewCommand}
              >
                确认下发{actionText}
              </Button>
              <Button variant="outline" onClick={() => setConfirming(false)}>
                取消下发
              </Button>
            </fieldset>
          )}
        </div>
      )}
      {idError && (
        <p role="alert">
          浏览器安全随机数不可用，本次没有发送；请使用受支持的浏览器。
        </p>
      )}
      {storageError && (
        <p role="alert">
          浏览器不能保存待确认编号，本次没有发送。请恢复浏览器存储后重试。
        </p>
      )}
      {pending && (
        <div role="status" className="space-y-2 rounded border p-3 text-sm">
          <p>待确认命令：{pending.command_id}</p>
          <p>
            原动作：{pending.enabled ? "开启制冷" : "关闭制冷"}，有效期{" "}
            {pending.lifetime_seconds} 秒。
          </p>
          <p>尚未确认终态；失败或查无记录时不自动生成新编号。</p>
          <Button
            variant="outline"
            disabled={detail.isFetching}
            onClick={() => {
              setSelectedId(pending.command_id)
              queryClient.invalidateQueries({
                queryKey: [
                  "device-command",
                  actorId,
                  device.id,
                  pending.command_id,
                ],
              })
            }}
          >
            查询原编号
          </Button>
          {canControl && !submission.isPending && submission.isError && (
            <Button
              variant="outline"
              onClick={() => {
                setSelectedId(pending.command_id)
                submission.mutate(pending)
              }}
            >
              使用原编号重试
            </Button>
          )}
          {canControl && !submission.isPending && submission.isIdle && (
            <Button
              variant="outline"
              onClick={() => submission.mutate(pending)}
            >
              使用原编号重试
            </Button>
          )}
          {submission.isPending && <p>请求发送中，尚未确认受理或执行。</p>}
        </div>
      )}
      {submission.isError && (
        <p role="alert" className="text-sm text-destructive">
          {controlError(submission.error)}
        </p>
      )}
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-sm font-medium">最近 5 条命令</h4>
        <Button
          size="sm"
          variant="outline"
          disabled={history.isFetching}
          onClick={() => history.refetch()}
        >
          刷新命令
        </Button>
      </div>
      {history.isPending ? (
        <p>正在加载控制记录…</p>
      ) : history.isError ? (
        <p role="alert">
          控制记录查询失败，不能解释为没有命令。{controlError(history.error)}
        </p>
      ) : history.data.count === 0 ? (
        <p>暂无控制记录；不代表设备状态正常。</p>
      ) : (
        <ol className="space-y-2 text-sm">
          {history.data.data.map((command) => (
            <li key={command.command_id} className="rounded border p-3">
              <p>
                {command.enabled ? "开启制冷" : "关闭制冷"} ·{" "}
                {commandLabels[command.status]}
              </p>
              <p className="break-all text-xs">{command.command_id}</p>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setSelectedId(command.command_id)}
              >
                查询此命令
              </Button>
            </li>
          ))}
        </ol>
      )}
      {selectedId && (
        <section
          aria-label="命令与回执详情"
          className="space-y-2 rounded border p-3 text-sm"
        >
          <p className="break-all">命令编号：{selectedId}</p>
          {detail.isPending ? (
            <p>正在查询回执…</p>
          ) : detail.isError ? (
            <p role="alert">
              当前回执查询失败或尚未找到，不能显示为执行成功。
              {controlError(detail.error)}
            </p>
          ) : (
            <>
              <p role="status">{commandLabels[detail.data.status]}</p>
              <p>下发：{deviceTime(detail.data.issued_at)}</p>
              <p>到期：{deviceTime(detail.data.expires_at)}</p>
              <p>发送尝试：{detail.data.attempt_count}</p>
              <p>
                MQTT 接受：{deviceTime(detail.data.transport_accepted_at)}
                （不是执行确认）
              </p>
              <p>有效回执接收：{deviceTime(detail.data.ack_received_at)}</p>
              {detail.data.failure_reason && (
                <p>失败原因码：{detail.data.failure_reason}</p>
              )}
              {detail.data.receipts.length === 0 ? (
                <p>暂无设备回执。</p>
              ) : (
                <ol className="space-y-2" aria-label="设备回执记录">
                  {detail.data.receipts.map((receipt, index) => (
                    <li
                      key={`${receipt.received_at}-${index}`}
                      className="rounded border p-2"
                    >
                      <p>
                        {receipt.outcome === "late"
                          ? "迟到回执（不更改原终态）"
                          : receipt.outcome === "conflict"
                            ? "冲突回执（不能作为执行确认）"
                            : "服务端已接受的设备回执"}
                      </p>
                      <p>
                        设备上报结果：
                        {receipt.result === "executed" ? "执行" : "失败"}；制冷
                        {receipt.cooling_enabled ? "开启" : "关闭"}
                      </p>
                      <p>执行时间：{deviceTime(receipt.executed_at)}</p>
                      <p>接收时间：{deviceTime(receipt.received_at)}</p>
                      {receipt.reason && <p>回执原因码：{receipt.reason}</p>}
                    </li>
                  ))}
                </ol>
              )}
            </>
          )}
        </section>
      )}
    </section>
  )
}
