'use client';

import { useEffect, useRef, useState } from 'react';
import { TreeSelect } from 'antd';

const STORAGE_KEY = 'grade-select-last-value';

/** 历史数据带上下学期后缀，选项粒度已收到年级，读取处统一洗掉 */
function stripSemester(value?: string): string | undefined {
  return value?.replace(/(上学期|下学期)$/, '');
}

function buildGradeNodes(school: string, count: number) {
  return Array.from({ length: count }, (_, i) => {
    const gradeValue = `${school}${i + 1}年级`;
    return { title: `${school} ${i + 1}年级`, value: gradeValue };
  });
}

export const GRADE_TREE = [
  { title: '小学', value: '小学', children: buildGradeNodes('小学', 6) },
  { title: '初中', value: '初中', children: buildGradeNodes('初中', 4) },
  { title: '高中', value: '高中', children: buildGradeNodes('高中', 3) },
  { title: '大学', value: '大学', children: buildGradeNodes('大学', 4) },
];

function readStored(): string | undefined {
  if (typeof window === 'undefined') return undefined;
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    return stripSemester(v || undefined);
  } catch {
    return undefined;
  }
}

type GradeSelectProps = React.ComponentProps<typeof TreeSelect> & {
  /** 是否记住上次选择并在表单场景自动填充（默认 true；列表筛选等场景应传 false） */
  remember?: boolean;
};

export default function GradeSelect(props: GradeSelectProps) {
  const { value, onChange, remember = true, ...rest } = props;
  const [remembered, setRemembered] = useState<string | undefined>(() => (remember ? readStored() : undefined));
  const appliedRef = useRef(false);

  const displayValue = remember ? (value ?? remembered) : value;

  // 表单场景：外部未传值时，自动用上次记住的选择（仅挂载时一次）
  useEffect(() => {
    if (remember && !appliedRef.current) {
      appliedRef.current = true;
      const stored = readStored();
      if (stored && !value) {
        (onChange as any)?.(stored);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [remember]);

  const handleChange = (v: any, ...restArgs: any[]) => {
    if (remember) {
      const next = (v as string) || undefined;
      setRemembered(next);
      try {
        if (next) window.localStorage.setItem(STORAGE_KEY, next);
        else window.localStorage.removeItem(STORAGE_KEY);
      } catch {}
    }
    (onChange as any)?.(v, ...restArgs);
  };

  return (
    <TreeSelect
      treeData={GRADE_TREE}
      placeholder="请选择年级"
      value={displayValue}
      onChange={handleChange}
      {...rest}
    />
  );
}
