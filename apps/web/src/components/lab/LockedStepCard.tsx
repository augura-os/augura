import { Lock } from "lucide-react";
import { useT } from "../../lib/i18n";
import { Badge } from "../ui/badge";
import { Card, CardDescription, CardHeader, CardTitle } from "../ui/card";

interface LockedStepCardProps {
  titleKey: string;
  descKey: string;
}

/** M2 锁态步占位卡：只说明将要上线什么，无任何假功能。 */
export function LockedStepCard({ titleKey, descKey }: LockedStepCardProps) {
  const t = useT();
  return (
    <Card>
      <CardHeader className="p-6">
        <CardTitle className="flex items-center gap-2">
          <Lock className="h-4 w-4 text-neutral-400" />
          {t(titleKey)}
          <Badge variant="secondary">{t("lab.locked.m2")}</Badge>
        </CardTitle>
        <CardDescription>{t(descKey)}</CardDescription>
      </CardHeader>
    </Card>
  );
}
