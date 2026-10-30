export function Footer() {
  const currentYear = new Date().getFullYear()

  return (
    <footer className="border-t py-4 px-6">
      <div className="flex flex-col items-center justify-between gap-4 sm:flex-row">
        <p className="text-muted-foreground text-sm">
          鲜链智控 · 苹果冷库监测与告警处置 · {currentYear}
        </p>
        <div className="flex items-center gap-4">
          <a
            href="https://github.com/fastapi/full-stack-fastapi-template"
            target="_blank"
            rel="noopener noreferrer"
            className="text-muted-foreground text-xs hover:text-foreground"
          >
            基于 FastAPI 全栈模板
          </a>
        </div>
      </div>
    </footer>
  )
}
