export type JobCategory = 'public_institution' | 'enterprise';

export type SourceType =
  | 'gov_official'
  | 'institution_official'
  | 'company_official'
  | 'verified_saas';

export type JobStatus = 'active' | 'expired' | 'closed';

export type BianzhiType =
  | '全额拨款事业编'
  | '差额补贴事业编'
  | '自收自支事业编'
  | '控制数/备案制'
  | '公开招聘(具体见公告)'
  | '未知';

export type EducationLevel =
  | '不限'
  | '中专/高中'
  | '大专'
  | '本科'
  | '硕士'
  | '博士'
  | '见公告';

export interface PublicAttachment {
  name: string;
  url: string;
  file_type?: string | null;
}

export interface PublicMeta {
  post_code?: string | null;
  department?: string | null;
  post_level?: string | null;
  bianzhi_type: BianzhiType;
  headcount?: number | null;
  education_min: EducationLevel;
  degree_min?: string | null;
  majors: string[];
  fresh_grad_only?: boolean | null;
  age_limit?: string | null;
  political_status?: string | null;
  exam_type?: string | null;
  apply_method?: string | null;
  apply_start_at?: string | null;
  written_exam_time?: string | null;
  interview_time?: string | null;
  other_requirements?: string | null;
  announcement_title?: string | null;
  attachments: PublicAttachment[];
}

export type JobWorkSchedule =
  | '周末双休'
  | '弹性工作'
  | '做四休三'
  | '大小周'
  | '单休'
  | '未明确';

export type RemotePolicy = '线下办公' | '混合办公' | '可远程' | '未明确';

export interface VerifiedBenefit {
  tag: string;
  evidence_quote: string;
  evidence_url?: string | null;
}

export interface EnterpriseMeta {
  company_id: string;
  work_schedule: JobWorkSchedule;
  work_hours?: string | null;
  salary_range?: string | null;
  remote_policy: RemotePolicy;
  department?: string | null;
  experience_req?: string | null;
  education_req?: string | null;
  benefits_verified: VerifiedBenefit[];
}

export interface JobItem {
  id: string;
  category: JobCategory;
  title: string;
  org_name: string;
  province: string;
  city: string;
  district?: string | null;
  source_url: string;
  apply_url?: string | null;
  source_name: string;
  source_type: SourceType;
  published_at?: string | null;
  deadline_at?: string | null;
  status: JobStatus;
  scraped_at: string;
  last_seen_at: string;
  content_hash: string;
  summary: string;
  tags: string[];
  public_meta?: PublicMeta | null;
  enterprise_meta?: EnterpriseMeta | null;
}

export interface JobDataset {
  schema_version: string;
  updated_at: string;
  items: JobItem[];
}

export type SaasPlatform =
  | 'moka'
  | 'beisen'
  | 'feishu'
  | 'greenhouse'
  | 'lever'
  | 'workday'
  | 'custom'
  | 'unknown';

export type CompanyWorkSchedule = '周末双休' | '弹性工作' | '做四休三' | '其他';

export interface BenefitClaim {
  label: string;
  evidence_quote: string;
  evidence_url: string;
  verified_at: string;
}

export interface CompanyItem {
  company_id: string;
  name: string;
  aliases?: string[];
  industry: string;
  cities: string[];
  career_url: string;
  saas_platform: SaasPlatform;
  work_schedule: CompanyWorkSchedule;
  benefit_claims: BenefitClaim[];
  status: 'active' | 'paused';
  notes?: string | null;
}

export interface CompanyDataset {
  schema_version: string;
  updated_at: string;
  items: CompanyItem[];
}

export interface SiteDataBundle {
  jobs: JobItem[];
  companies: CompanyItem[];
  jobsUpdatedAt: string | null;
  companiesUpdatedAt: string | null;
  latestUpdatedAt: string | null;
  isPreviewFixtures: boolean;
}
