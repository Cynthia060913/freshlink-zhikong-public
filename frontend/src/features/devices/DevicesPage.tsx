import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { type FormEvent, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import useAuth from "@/hooks/useAuth"
import { type DeviceKind, deviceError, devicesApi, deviceTime } from "./api"

const kindNames: Record<DeviceKind, string> = {
  temperature_sensor: "温度测点",
  cooling_actuator: "制冷执行器",
}
const pageSize = 20

export default function DevicesPage() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(0)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [code, setCode] = useState("")
  const [name, setName] = useState("")
  const [kind, setKind] = useState<DeviceKind>("temperature_sensor")
  const [success, setSuccess] = useState<string | null>(null)
  const list = useQuery({
    queryKey: ["devices", page],
    queryFn: () => devicesApi.list(page * pageSize, pageSize),
  })
  const detail = useQuery({
    queryKey: ["device", selectedId],
    queryFn: () => devicesApi.detail(selectedId!),
    enabled: selectedId !== null,
  })
  const registration = useMutation({
    mutationFn: devicesApi.register,
    onSuccess: (device) => {
      setSuccess(`已注册 ${device.name}；尚未收到心跳，不能判定在线。`)
      setCode("")
      setName("")
      setPage(0)
      queryClient.invalidateQueries({ queryKey: ["devices"] })
    },
  })
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSuccess(null)
    registration.mutate({ code: code.trim(), name: name.trim(), kind })
  }

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-bold tracking-tight">设备管理</h1>
        <p className="mt-1 text-muted-foreground">
          鲜链智控 · 软件仿真设备登记与查询
        </p>
      </header>
      <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-4 text-sm">
        当前仅完成注册与查询。设备均为仿真设备，冷库尚未绑定，未接入心跳、MQTT
        和控制；“未连接/未知”不代表正常在线。
      </div>
      {user?.is_superuser ? (
        <section className="rounded-lg border p-5">
          <h2 className="mb-4 font-semibold">注册设备</h2>
          <form
            onSubmit={submit}
            className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"
          >
            <div className="space-y-2">
              <Label htmlFor="device-code">设备编号</Label>
              <Input
                id="device-code"
                value={code}
                onChange={(e) => {
                  setCode(e.target.value)
                  registration.reset()
                  setSuccess(null)
                }}
                required
                maxLength={64}
                pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,63}"
                placeholder="例如 temp-01"
                disabled={registration.isPending}
              />
              <p className="text-xs text-muted-foreground">
                字母、数字、下划线或连字符；保存为小写
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="device-name">设备名称</Label>
              <Input
                id="device-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                maxLength={120}
                placeholder="例如 一号温度测点"
                disabled={registration.isPending}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="device-kind">设备类型</Label>
              <select
                id="device-kind"
                value={kind}
                onChange={(e) => setKind(e.target.value as DeviceKind)}
                className="h-9 w-full rounded-md border bg-background px-3 text-sm"
                disabled={registration.isPending}
              >
                <option value="temperature_sensor">温度测点</option>
                <option value="cooling_actuator">制冷执行器</option>
              </select>
            </div>
            <div className="flex items-start pt-6">
              <Button
                type="submit"
                disabled={
                  registration.isPending || !code.trim() || !name.trim()
                }
              >
                {registration.isPending ? "正在注册…" : "注册设备"}
              </Button>
            </div>
          </form>
          {registration.isError && (
            <p role="alert" className="mt-3 text-sm text-destructive">
              {deviceError(registration.error)}
            </p>
          )}
          {success && (
            <p role="status" className="mt-3 text-sm text-emerald-500">
              {success}
            </p>
          )}
        </section>
      ) : (
        <p className="text-sm text-muted-foreground">
          当前为只读访问；管理员可注册设备。
        </p>
      )}
      <section className="rounded-lg border p-5">
        <div className="mb-4 flex items-center justify-between gap-3">
          <h2 className="font-semibold">
            设备列表{list.data ? `（共 ${list.data.count} 台）` : ""}
          </h2>
          <Button
            variant="outline"
            onClick={() => list.refetch()}
            disabled={list.isFetching}
          >
            {list.isFetching ? "加载中…" : "刷新"}
          </Button>
        </div>
        {list.isPending ? (
          <p role="status">正在加载设备…</p>
        ) : list.isError ? (
          <div role="alert" className="text-destructive">
            {deviceError(list.error)}
            <p className="mt-2 text-xs">
              请使用“刷新”重试；失败状态不显示为在线或正常。
            </p>
          </div>
        ) : list.data.data.length === 0 ? (
          <p className="py-6 text-center text-muted-foreground">
            暂无设备，请管理员先注册。
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b">
                  <th className="p-3">编号 / 名称</th>
                  <th className="p-3">类型</th>
                  <th className="p-3">连接状态</th>
                  <th className="p-3">冷库</th>
                  <th className="p-3">操作</th>
                </tr>
              </thead>
              <tbody>
                {list.data.data.map((device) => (
                  <tr key={device.id} className="border-b last:border-0">
                    <td className="p-3">
                      <div className="font-medium">{device.name}</div>
                      <div className="text-xs text-muted-foreground">
                        {device.code}
                      </div>
                    </td>
                    <td className="p-3">
                      {kindNames[device.kind]}
                      <div className="text-xs text-muted-foreground">
                        软件仿真
                      </div>
                    </td>
                    <td className="p-3">
                      <span className="rounded bg-muted px-2 py-1">
                        未连接 / 未知
                      </span>
                    </td>
                    <td className="p-3">{device.warehouse_id ?? "未绑定"}</td>
                    <td className="p-3">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setSelectedId(device.id)}
                      >
                        查看详情
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
          if (!open) setSelectedId(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>设备详情</DialogTitle>
            <DialogDescription>
              时间使用北京时间；尚未接入真实心跳或控制。
            </DialogDescription>
          </DialogHeader>
          {detail.isPending ? (
            <p>正在加载详情…</p>
          ) : detail.isError ? (
            <div role="alert">
              {deviceError(detail.error)}
              <Button variant="outline" onClick={() => detail.refetch()}>
                重试
              </Button>
            </div>
          ) : (
            detail.data && (
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-3 text-sm">
                <dt>名称</dt>
                <dd>{detail.data.name}</dd>
                <dt>编号</dt>
                <dd>{detail.data.code}</dd>
                <dt>设备 ID</dt>
                <dd className="break-all">{detail.data.id}</dd>
                <dt>类型</dt>
                <dd>{kindNames[detail.data.kind]}</dd>
                <dt>数据来源</dt>
                <dd>软件仿真</dd>
                <dt>连接状态</dt>
                <dd>未连接 / 未知</dd>
                <dt>冷库</dt>
                <dd>{detail.data.warehouse_id ?? "未绑定"}</dd>
                <dt>注册时间</dt>
                <dd>{deviceTime(detail.data.created_at)}</dd>
                <dt>最近心跳</dt>
                <dd>{deviceTime(detail.data.last_seen_at)}</dd>
              </dl>
            )
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
