import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SURVEY_YEARS } from "@/lib/differ/types";

const ALL_YEARS_VALUE = "all";

export function YearSelector({
  value,
  onChange,
}: {
  value: number | null;
  onChange: (year: number | null) => void;
}) {
  return (
    <Select
      value={value === null ? ALL_YEARS_VALUE : String(value)}
      onValueChange={(v) => onChange(v === ALL_YEARS_VALUE ? null : Number(v))}
    >
      <SelectTrigger className="w-[140px]">
        <SelectValue placeholder="All years" />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL_YEARS_VALUE}>All years</SelectItem>
        {SURVEY_YEARS.map((year) => (
          <SelectItem key={year} value={String(year)}>
            {year}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
