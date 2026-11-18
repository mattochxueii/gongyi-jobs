import fs from 'node:fs';
import path from 'node:path';
import type {
  CompanyDataset,
  CompanyItem,
  JobDataset,
  JobItem,
  SiteDataBundle,
} from './types';

function resolveRepoRoot(): string {
  const candidates = [
    path.resolve(process.cwd(), '..'),
    process.cwd(),
  ];

  for (const candidate of candidates) {
    if (
      fs.existsSync(path.join(candidate, 'data', 'jobs.json')) &&
      fs.existsSync(path.join(candidate, 'data', 'companies.json'))
    ) {
      return candidate;
    }
  }

  return path.resolve(process.cwd(), '..');
}

function readJsonSafe<T>(filePath: string): T | null {
  try {
    if (!fs.existsSync(filePath)) return null;
    const raw = fs.readFileSync(filePath, 'utf-8');
    return JSON.parse(raw) as T;
  } catch (err) {
    console.warn(`[site/data] Failed to read JSON at ${filePath}:`, err);
    return null;
  }
}

function pickLatestIsoTimestamp(a: string | null, b: string | null): string | null {
  if (!a) return b;
  if (!b) return a;
  const ta = Date.parse(a);
  const tb = Date.parse(b);
  if (Number.isNaN(ta)) return b;
  if (Number.isNaN(tb)) return a;
  return ta >= tb ? a : b;
}

/**
 * Load jobs and companies datasets at Astro build time.
 * Production builds ALWAYS read ../data/jobs.json and ../data/companies.json.
 * Preview fixture data is loaded ONLY when PREVIEW_FIXTURES=1 is explicitly set.
 */
export function loadSiteData(): SiteDataBundle {
  const repoRoot = resolveRepoRoot();
  const isPreviewFixtures = process.env.PREVIEW_FIXTURES === '1';

  if (isPreviewFixtures) {
    const previewPath = path.join(
      repoRoot,
      'tests',
      'fixtures',
      'preview_site_dataset.json',
    );
    const fallbackSamplePath = path.join(
      repoRoot,
      'tests',
      'fixtures',
      'sample_jobs_batch.json',
    );

    const previewRaw =
      readJsonSafe<{
        updated_at?: string;
        jobs?: JobItem[];
        companies?: CompanyItem[];
      }>(previewPath) ??
      readJsonSafe<{
        updated_at?: string;
        jobs?: JobItem[];
        companies?: CompanyItem[];
      }>(fallbackSamplePath);

    const jobs = Array.isArray(previewRaw?.jobs) ? previewRaw.jobs : [];
    const companies = Array.isArray(previewRaw?.companies) ? previewRaw.companies : [];
    const updatedAt =
      typeof previewRaw?.updated_at === 'string'
        ? previewRaw.updated_at
        : '2026-11-18T12:48:00+08:00';

    return {
      jobs,
      companies,
      jobsUpdatedAt: updatedAt,
      companiesUpdatedAt: updatedAt,
      latestUpdatedAt: updatedAt,
      isPreviewFixtures: true,
    };
  }

  const jobsPath = path.join(repoRoot, 'data', 'jobs.json');
  const companiesPath = path.join(repoRoot, 'data', 'companies.json');

  const jobsDataset = readJsonSafe<JobDataset>(jobsPath);
  const companiesDataset = readJsonSafe<CompanyDataset>(companiesPath);

  const jobs = Array.isArray(jobsDataset?.items) ? jobsDataset.items : [];
  const companies = Array.isArray(companiesDataset?.items) ? companiesDataset.items : [];
  const jobsUpdatedAt =
    typeof jobsDataset?.updated_at === 'string' ? jobsDataset.updated_at : null;
  const companiesUpdatedAt =
    typeof companiesDataset?.updated_at === 'string' ? companiesDataset.updated_at : null;

  return {
    jobs,
    companies,
    jobsUpdatedAt,
    companiesUpdatedAt,
    latestUpdatedAt: pickLatestIsoTimestamp(jobsUpdatedAt, companiesUpdatedAt),
    isPreviewFixtures: false,
  };
}

/**
 * Format an ISO timestamp in Asia/Shanghai (UTC+8) for display.
 */
export function formatTimestampCST(iso: string | null | undefined): string {
  if (!iso) return '等待首次同步';
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;

  try {
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
      .format(parsed)
      .replace(/\//g, '-');
  } catch {
    return iso.slice(0, 16).replace('T', ' ');
  }
}

export interface DeadlineInfo {
  state: 'upcoming' | 'today' | 'expired' | 'unknown';
  label: string;
  diffDays: number | null;
}

/**
 * Compute deadline countdown relative to a YYYY-MM-DD date in Asia/Shanghai.
 */
export function getDeadlineInfo(
  deadlineAt: string | null | undefined,
  status: JobItem['status'],
  todayYmd?: string,
): DeadlineInfo {
  if (status === 'expired' || status === 'closed') {
    return {
      state: 'expired',
      label: status === 'closed' ? '已关闭' : '已截止',
      diffDays: -1,
    };
  }

  if (!deadlineAt || !/^\d{4}-\d{2}-\d{2}$/.test(deadlineAt)) {
    return {
      state: 'unknown',
      label: '截止时间见公告',
      diffDays: null,
    };
  }

  const refYmd =
    todayYmd ??
    new Intl.DateTimeFormat('sv-SE', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(new Date());

  const [dy, dm, dd] = deadlineAt.split('-').map(Number);
  const [ry, rm, rd] = refYmd.split('-').map(Number);
  const targetUtc = Date.UTC(dy, dm - 1, dd);
  const refUtc = Date.UTC(ry, rm - 1, rd);
  const diffDays = Math.round((targetUtc - refUtc) / 86_400_000);

  if (diffDays < 0) {
    return { state: 'expired', label: '已截止', diffDays };
  }
  if (diffDays === 0) {
    return { state: 'today', label: '今日截止', diffDays: 0 };
  }
  return { state: 'upcoming', label: `剩 ${diffDays} 天`, diffDays };
}

export const SOURCE_TYPE_LABELS: Record<JobItem['source_type'], string> = {
  gov_official: '政务官网',
  institution_official: '事业单位官网',
  company_official: '企业官网',
  verified_saas: '官方招聘SaaS',
};

export const SAAS_PLATFORM_LABELS: Record<CompanyItem['saas_platform'], string> = {
  moka: 'Moka 招聘',
  beisen: '北森招聘',
  feishu: '飞书招聘',
  greenhouse: 'Greenhouse',
  lever: 'Lever',
  workday: 'Workday',
  custom: '企业自建官网',
  unknown: '官方通道',
};
