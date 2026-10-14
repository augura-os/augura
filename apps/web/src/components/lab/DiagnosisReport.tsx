import { useState, type ReactNode } from "react";
import { AlertTriangle, ChevronDown, ChevronUp } from "lucide-react";
import type { LabDiagnosis } from "@shared";
import { labEnumLabel, UNCERTAIN_IDS } from "../../lib/labEnums";
import { useT } from "../../lib/i18n";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../ui/table";

/** 枚举 chip：词表 id（淡色小字）+ 展示名；判不出（uncertain/unclear）用警示色。 */
function EnumChip({ group, id }: { group: string; id: string }) {
  const label = labEnumLabel(group, id);
  const uncertain = UNCERTAIN_IDS.has(id);
  return (
    <Badge variant={uncertain ? "warning" : "secondary"} className="gap-1">
      <span className={uncertain ? "text-amber-500" : "text-neutral-400"}>{id}</span>
      {label !== id ? <span>{label}</span> : null}
    </Badge>
  );
}

/** 「标签 + 内容」行：报告各区块的通用排版。 */
function FieldRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-3">
      <span className="w-28 shrink-0 pt-0.5 text-xs text-neutral-500">{label}</span>
      <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-sm text-neutral-800">
        {children}
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2.5">{children}</CardContent>
    </Card>
  );
}

function StringList({ items, className }: { items: string[]; className?: string }) {
  if (items.length === 0) return <span className="text-xs text-neutral-400">—</span>;
  return (
    <ul className={cn("list-disc space-y-0.5 pl-5 text-sm text-neutral-800", className)}>
      {items.map((item, index) => (
        <li key={index}>{item}</li>
      ))}
    </ul>
  );
}

/** 可迁移结构三列之一；danger = do_not_copy 列（警示色）。 */
function TransferColumn({
  title,
  items,
  danger = false,
}: {
  title: string;
  items: string[];
  danger?: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-md border px-2.5 py-2",
        danger ? "border-red-200 bg-red-50" : "border-[#e5e5e5] bg-neutral-50",
      )}
    >
      <p className={cn("mb-1 text-xs font-medium", danger ? "text-red-700" : "text-neutral-600")}>
        {title}
      </p>
      {items.length === 0 ? (
        <span className="text-xs text-neutral-400">—</span>
      ) : (
        <ul
          className={cn(
            "list-disc space-y-0.5 pl-4 text-xs",
            danger ? "text-red-700" : "text-neutral-700",
          )}
        >
          {items.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface DiagnosisReportProps {
  diagnosis: LabDiagnosis;
}

/** 六要素定性报告（结论先行）：verdict → 定性 → 结构 → 内容 → 情绪 →
 *  可迁移结构 → 卖点映射 → 不确定项（折叠）。 */
export function DiagnosisReport({ diagnosis }: DiagnosisReportProps) {
  const t = useT();
  const [uncertaintiesOpen, setUncertaintiesOpen] = useState(false);
  const out = diagnosis.output;
  const cls = out.classification;
  const structure = out.structure;
  const content = out.content;
  const emotion = out.emotion;
  const transferable = out.transferable_structure;
  const selling = out.selling_point_candidates;
  const uncertainties = out.uncertainties ?? [];
  const motifUnclear = UNCERTAIN_IDS.has(cls.motif);

  return (
    <div className="space-y-4">
      {/* ① verdict：一句话结论大字块 */}
      <Card>
        <CardContent className="p-4">
          <div className="flex items-center justify-between gap-2">
            <p className="text-[11px] font-medium uppercase tracking-wide text-neutral-400">
              {t("lab.report.verdict")}
            </p>
            <EnumChip group="evidence" id={diagnosis.evidence_label} />
          </div>
          <p className="mt-1 text-base font-medium leading-relaxed text-neutral-900">
            {out.verdict}
          </p>
        </CardContent>
      </Card>

      {/* ② 定性：chips 行 + 母题 */}
      <Section title={t("lab.report.classification")}>
        <div
          className={cn(
            "rounded-md px-2.5 py-1.5 text-sm",
            motifUnclear ? "bg-amber-50 text-amber-800" : "bg-neutral-50 text-neutral-800",
          )}
        >
          <span className="mr-2 text-xs text-neutral-500">{t("lab.report.motif")}</span>
          {motifUnclear ? t("lab.report.motifUnclear") : cls.motif}
        </div>
        {cls.hook_reason ? <p className="text-xs text-neutral-500">{cls.hook_reason}</p> : null}
        <FieldRow label={t("lab.report.hookMechanism")}>
          <EnumChip group="hook_mechanism" id={cls.hook_mechanism} />
        </FieldRow>
        {cls.form_tags.length > 0 ? (
          <FieldRow label={t("lab.report.formTags")}>
            {cls.form_tags.map((id) => (
              <EnumChip key={id} group="form_tag" id={id} />
            ))}
          </FieldRow>
        ) : null}
        <FieldRow label={t("lab.report.timeliness")}>
          <EnumChip group="timeliness" id={cls.timeliness} />
        </FieldRow>
        <FieldRow label={t("lab.report.opening3s")}>
          <EnumChip group="opening_3s" id={cls.opening_3s_solution} />
          {cls.opening_3s_relation_ok === true ? (
            <Badge variant="success">{t("lab.report.relationOk")}</Badge>
          ) : null}
          {cls.opening_3s_relation_ok === false ? (
            <Badge variant="destructive">{t("lab.report.relationNotOk")}</Badge>
          ) : null}
        </FieldRow>
        <FieldRow label={t("lab.report.brandImplant")}>
          <EnumChip group="brand_implant" id={cls.brand_implant_level} />
        </FieldRow>
      </Section>

      {/* ③ 结构：segments 时间轴 + 桥接/语法/路线/冲突触发点 */}
      <Section title={t("lab.report.structure")}>
        <div className="space-y-1.5">
          {structure.segments.map((segment, index) => (
            <div key={index} className="flex items-start gap-2.5">
              <Badge
                variant={
                  segment.kind === "A"
                    ? "default"
                    : segment.kind === "B"
                      ? "secondary"
                      : "outline"
                }
              >
                {segment.kind}
              </Badge>
              <span className="w-20 shrink-0 pt-0.5 font-mono text-xs text-neutral-500">
                {segment.start_s}–{segment.end_s}s
              </span>
              <p className="min-w-0 text-sm text-neutral-800">{segment.summary}</p>
            </div>
          ))}
        </div>
        <FieldRow label={t("lab.report.bridgeMode")}>
          <EnumChip group="bridge_mode" id={structure.ab_bridge_mode} />
        </FieldRow>
        {(structure.b_grammar_narrative ?? []).length > 0 ? (
          <FieldRow label={t("lab.report.bGrammarNarrative")}>
            {(structure.b_grammar_narrative ?? []).map((id) => (
              <EnumChip key={id} group="b_narrative" id={id} />
            ))}
          </FieldRow>
        ) : null}
        {(structure.b_grammar_ui ?? []).length > 0 ? (
          <FieldRow label={t("lab.report.bGrammarUi")}>
            {(structure.b_grammar_ui ?? []).map((id) => (
              <EnumChip key={id} group="b_ui" id={id} />
            ))}
          </FieldRow>
        ) : null}
        {structure.visual_route ? (
          <FieldRow label={t("lab.report.visualRoute")}>
            <EnumChip group="visual_route" id={structure.visual_route} />
          </FieldRow>
        ) : null}
        <FieldRow label={t("lab.report.conflictTrigger")}>
          <span className="text-sm">{structure.conflict_trigger}</span>
        </FieldRow>
      </Section>

      {/* ④ 内容：设定/角色/关键画面 + 选择链表 + 强反馈点 */}
      <Section title={t("lab.report.content")}>
        <FieldRow label={t("lab.report.setting")}>
          <span className="text-sm">{content.setting}</span>
        </FieldRow>
        {content.characters ? (
          <FieldRow label={t("analysis.characters")}>
            <span className="text-sm">{content.characters}</span>
          </FieldRow>
        ) : null}
        {content.key_visuals.length > 0 ? (
          <FieldRow label={t("lab.report.keyVisuals")}>
            {content.key_visuals.map((visual) => (
              <Badge key={visual} variant="outline">
                {visual}
              </Badge>
            ))}
          </FieldRow>
        ) : null}
        {(content.choice_chain ?? []).length > 0 ? (
          <div>
            <p className="mb-1 text-xs text-neutral-500">{t("lab.report.choiceChain")}</p>
            <div className="overflow-hidden rounded-md border border-[#e5e5e5]">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("lab.report.choiceBeat")}</TableHead>
                    <TableHead>{t("lab.report.choiceCol")}</TableHead>
                    <TableHead>{t("lab.report.choiceFeedback")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(content.choice_chain ?? []).map((beat, index) => {
                    const good = beat.choice === "正解";
                    return (
                      <TableRow key={index}>
                        <TableCell className="text-sm">{beat.beat}</TableCell>
                        <TableCell className="whitespace-nowrap">
                          <span className={good ? "text-emerald-600" : "text-red-600"}>
                            {good ? "✅" : "❌"} {labEnumLabel("choice", beat.choice)}
                          </span>
                        </TableCell>
                        <TableCell className="text-sm">{beat.feedback}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          </div>
        ) : null}
        {(content.strong_feedback_points ?? []).length > 0 ? (
          <div>
            <p className="mb-1 text-xs text-neutral-500">{t("lab.report.strongFeedback")}</p>
            <StringList items={content.strong_feedback_points ?? []} />
          </div>
        ) : null}
      </Section>

      {/* ⑤ 情绪：主/辅入口 + 曲线 + 威胁阶梯 */}
      <Section title={t("lab.report.emotion")}>
        <FieldRow label={t("lab.report.primaryEntry")}>
          <EnumChip group="emotion_entry" id={emotion.primary_entry} />
        </FieldRow>
        {emotion.secondary_entry ? (
          <FieldRow label={t("lab.report.secondaryEntry")}>
            <EnumChip group="emotion_entry" id={emotion.secondary_entry} />
          </FieldRow>
        ) : null}
        <FieldRow label={t("lab.report.emotionCurve")}>
          <span className="text-sm">{emotion.emotion_curve}</span>
        </FieldRow>
        {emotion.threat_ladder ? (
          <FieldRow label={t("lab.report.threatLadder")}>
            <span className="text-sm">{emotion.threat_ladder}</span>
          </FieldRow>
        ) : null}
      </Section>

      {/* ⑥ 可迁移结构：fixed / reskinnable / do_not_copy 三列 */}
      <Section title={t("lab.report.transferable")}>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <TransferColumn title={t("lab.report.fixed")} items={transferable.fixed} />
          <TransferColumn title={t("lab.report.reskinnable")} items={transferable.reskinnable} />
          <TransferColumn title={t("lab.report.doNotCopy")} items={transferable.do_not_copy} danger />
        </div>
      </Section>

      {/* ⑦ 卖点映射：可映射点 + 光谱层 + 缺口 + 缺失清单（警示） */}
      <Section title={t("lab.report.sellingPoints")}>
        <FieldRow label={t("lab.report.mappablePoints")}>
          {selling.mappable_points.length > 0 ? (
            selling.mappable_points.map((point) => (
              <Badge key={point} variant="success">
                {point}
              </Badge>
            ))
          ) : (
            <span className="text-xs text-neutral-400">—</span>
          )}
        </FieldRow>
        <FieldRow label={t("lab.report.spectrumLayer")}>
          <EnumChip group="spectrum" id={selling.spectrum_layer} />
        </FieldRow>
        {(selling.mapping_gaps ?? []).length > 0 ? (
          <div>
            <p className="mb-1 text-xs text-neutral-500">{t("lab.report.mappingGaps")}</p>
            <StringList items={selling.mapping_gaps ?? []} />
          </div>
        ) : null}
        {(selling.missing_product_facts ?? []).length > 0 ? (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5">
            <p className="mb-1 flex items-center gap-1 text-xs font-medium text-amber-800">
              <AlertTriangle className="h-3.5 w-3.5" />
              {t("lab.report.missingFacts")}
            </p>
            <ul className="list-disc space-y-0.5 pl-5 text-xs text-amber-800">
              {(selling.missing_product_facts ?? []).map((fact, index) => (
                <li key={index}>{fact}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </Section>

      {/* ⑧ 不确定项（默认折叠）+ 版本审计行 */}
      {uncertainties.length > 0 ? (
        <div className="rounded-lg border border-[#e5e5e5] bg-white">
          <button
            type="button"
            onClick={() => setUncertaintiesOpen((open) => !open)}
            className="flex w-full items-center justify-between px-4 py-2.5 text-xs font-medium text-neutral-600 hover:text-neutral-900"
          >
            {t("lab.report.uncertainties").replace("{n}", String(uncertainties.length))}
            {uncertaintiesOpen ? (
              <ChevronUp className="h-3.5 w-3.5" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5" />
            )}
          </button>
          {uncertaintiesOpen ? (
            <ul className="list-disc space-y-0.5 border-t border-[#e5e5e5] px-4 py-2.5 pl-9 text-xs text-neutral-600">
              {uncertainties.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <p className="text-xs text-neutral-400">
        {t("lab.report.meta")
          .replace("{prompt}", diagnosis.prompt_version)
          .replace("{engine}", diagnosis.engine_version)
          .replace("{date}", new Date(diagnosis.created_at).toLocaleString())}
      </p>
    </div>
  );
}
