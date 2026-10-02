import { Component, Input, OnChanges, OnDestroy, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { forkJoin, Subscription } from 'rxjs';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { ApiService } from './api.service';
import {
  AuditEvent,
  CommercialCustomer,
  CommercialDevice,
  CommercialGrant,
  CommercialLease,
  CommercialPlan,
  CommercialSection,
  CommercialSnapshot,
  CommercialSource,
  DashboardCustomer,
  DashboardSnapshot,
  SubscriptionAddressStatus,
} from './models';

type CustomerStatusFilter = 'all' | 'active' | 'disabled' | 'archived';
type LeaseStatusFilter = 'all' | 'active' | 'expired' | 'revoked';

interface ConfirmationRequest {
  title: string;
  description: string;
  consequence: string;
  payload: Record<string, unknown>;
  success: string;
  after?: (value: any) => void;
}

const EMPTY_SNAPSHOT: CommercialSnapshot = {
  sources: [],
  plans: [],
  customers: [],
  subscription_addresses: [],
  devices: [],
  grants: [],
  leases: [],
};

@Component({
  selector: 'app-commercial',
  imports: [
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatCheckboxModule,
    MatExpansionModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
  ],
  templateUrl: './commercial.component.html',
  styleUrl: './commercial.component.scss',
})
export class CommercialComponent implements OnChanges, OnDestroy {
  @Input() active = false;
  @Input() section: CommercialSection = 'overview';

  readonly snapshot = signal<CommercialSnapshot>(EMPTY_SNAPSHOT);
  readonly dashboard = signal<DashboardSnapshot | null>(null);
  readonly events = signal<AuditEvent[]>([]);
  readonly loading = signal(false);
  readonly busy = signal(false);
  readonly message = signal('');
  readonly error = signal('');
  readonly selectedCustomer = signal<DashboardCustomer | null>(null);
  readonly pendingAction = signal<ConfirmationRequest | null>(null);
  readonly oneTimeResult = signal<any | null>(null);
  readonly fingerprints = signal<Record<string, string>>({});
  readonly now = signal(Math.floor(Date.now() / 1000));

  customerSearch = '';
  customerStatus: CustomerStatusFilter = 'all';
  customerTab = 'overview';
  leaseStatus: LeaseStatusFilter = 'active';
  reauthPassword = '';
  wizardStep = 1;
  subscriptionMode: 'new' | 'reissue' = 'new';
  customerName = '';
  reissueCustomerId = '';
  baseUrl = 'http://10.20.32.13:9182';
  selectedSourceIds: string[] = [];
  proxySourceIds: string[] = [];
  quotaGb: number | null = 50;
  downloadMbps = 50;
  uploadMbps = 10;
  sourceDraft: any = null;
  grantDraft: any = null;
  private read?: Subscription;
  private lastSection?: CommercialSection;
  private readonly clock = setInterval(() => this.now.set(Math.floor(Date.now() / 1000)), 30_000);

  readonly quotas = [
    { label: '10 GB', value: 10 },
    { label: '50 GB', value: 50 },
    { label: '100 GB', value: 100 },
    { label: '无限 GB', value: null },
  ];

  constructor(private readonly api: ApiService) {}

  ngOnChanges() {
    if (!this.active) {
      this.read?.unsubscribe();
      return;
    }
    if (this.lastSection !== this.section) {
      this.message.set('');
      this.error.set('');
      this.lastSection = this.section;
    }
    this.reload();
  }

  ngOnDestroy() {
    this.read?.unsubscribe();
    clearInterval(this.clock);
  }

  reload(force = false) {
    if (force) this.api.invalidateNavigation();
    this.read?.unsubscribe();
    this.loading.set(true);
    this.error.set('');
    this.read = forkJoin({
      data: this.api.get<CommercialSnapshot>('/api/client-service'),
      dashboard: this.api.get<DashboardSnapshot>('/api/client-service/dashboard'),
      audit: this.api.get<{ events: AuditEvent[] }>('/api/audit'),
    }).subscribe({
      next: (value) => {
        this.snapshot.set(value.data);
        this.dashboard.set(value.dashboard);
        this.events.set(value.audit.events || []);
        const selectedId = this.selectedCustomer()?.id;
        if (selectedId) {
          const refreshed = value.dashboard.customers?.find((customer) => customer.id === selectedId)
            || value.data.customers.find((customer) => customer.id === selectedId);
          if (refreshed) this.selectedCustomer.set(this.enrichCustomer(refreshed));
        }
        this.loading.set(false);
        void this.loadFingerprints(value.data.devices || []);
      },
      error: (error) => {
        if (error?.name === 'AbortError' || error?.message === 'Obsolete read') return;
        this.loading.set(false);
        this.error.set(error.message);
      },
    });
  }

  get customerRows(): DashboardCustomer[] {
    const fromDashboard = this.dashboard()?.customers || [];
    if (fromDashboard.length) return fromDashboard;
    return this.snapshot().customers.map((customer) => this.enrichCustomer(customer));
  }

  get filteredCustomers(): DashboardCustomer[] {
    const query = this.customerSearch.trim().toLowerCase();
    return this.customerRows.filter((customer) => {
      const matchesQuery = !query || `${customer.display_name} ${customer.id}`.toLowerCase().includes(query);
      const status = this.customerStatusOf(customer);
      const matchesStatus = this.customerStatus === 'all' || status === this.customerStatus;
      return matchesQuery && matchesStatus;
    });
  }

  get filteredLeases(): CommercialLease[] {
    return this.snapshot().leases.filter((lease) => {
      const status = this.leaseStatusOf(lease);
      return this.leaseStatus === 'all' || status === this.leaseStatus;
    });
  }

  get recentEvents(): AuditEvent[] {
    return this.events()
      .filter((event) => event.action.startsWith('client_'))
      .slice()
      .sort((a, b) => b.created_at - a.created_at)
      .slice(0, 6);
  }

  get recentDevices(): CommercialDevice[] {
    return this.snapshot().devices.slice().sort((a, b) => b.created_at - a.created_at).slice(0, 5);
  }

  get recentLeases(): CommercialLease[] {
    return this.snapshot().leases.slice().sort((a, b) => b.issued_at - a.issued_at).slice(0, 5);
  }

  get validNodeCount() {
    return this.snapshot().sources.filter((source) => this.sourceEnabled(source)).length;
  }

  get activeCustomerCount() {
    return this.customerRows.filter((customer) => this.customerStatusOf(customer) === 'active').length;
  }

  get activeLeaseCount() {
    return this.snapshot().leases.filter((lease) => this.leaseStatusOf(lease) === 'active').length;
  }

  get directoryRevision() {
    return '未接入';
  }

  get selectedCustomerView() {
    return this.selectedCustomer();
  }

  get availableSources() {
    return this.snapshot().sources.filter((source) => this.sourceEnabled(source));
  }

  get planRows(): CommercialPlan[] {
    return this.snapshot().plans.slice().sort((a, b) => b.created_at - a.created_at);
  }

  get sourceProxy() {
    return this.snapshot().source_proxy || {};
  }

  get pending() {
    return this.pendingAction();
  }

  pageTitle() {
    return ({
      overview: '商业服务概览',
      customers: '客户',
      subscriptions: '订阅与套餐',
      devices: '设备',
      nodes: '节点与出口',
      leases: '租约',
      directory: '目录发布',
    } as Record<CommercialSection, string>)[this.section];
  }

  customerStatusOf(customer: Partial<DashboardCustomer>) {
    if (customer.lifecycle === 'archived' || customer.lifecycle === 'deleted') return 'archived';
    return this.isEnabled(customer.enabled) ? 'active' : 'disabled';
  }

  customerStatusLabel(customer: Partial<DashboardCustomer>) {
    const status = this.customerStatusOf(customer);
    return status === 'active' ? '启用' : status === 'disabled' ? '服务停用' : customer.lifecycle === 'deleted' ? '已回收' : '已归档';
  }

  customerStatusClass(customer: Partial<DashboardCustomer>) {
    return `status-${this.customerStatusOf(customer)}`;
  }

  customerPlan(customer: Partial<DashboardCustomer>) {
    return customer.plan || this.snapshot().plans.find((plan) => plan.id === customer.plan_id);
  }

  customerExpiry(customer: DashboardCustomer | CommercialCustomer) {
    const grants = this.snapshot().grants.filter((grant) => grant.customer_id === customer.id);
    const values = grants.map((grant) => grant.expires_at).filter((value): value is number => typeof value === 'number' && value > 0);
    return values.length ? Math.min(...values) : null;
  }

  customerDevices(customer: DashboardCustomer | CommercialCustomer) {
    return this.snapshot().devices.filter((device) => device.customer_id === customer.id);
  }

  customerLeases(customer: DashboardCustomer | CommercialCustomer) {
    return this.snapshot().leases.filter((lease) => lease.customer_id === customer.id);
  }

  customerGrants(customer: DashboardCustomer | CommercialCustomer) {
    return this.snapshot().grants.filter((grant) => grant.customer_id === customer.id);
  }

  customerUsage(customer: DashboardCustomer) {
    return customer.usage || { used_bytes: 0, remaining_bytes: null, quota_bytes: null };
  }

  subscriptionStatus(customerId: string): SubscriptionAddressStatus | undefined {
    return this.snapshot().subscription_addresses.find((status) => status.customer_id === customerId);
  }

  openCustomer(customer: DashboardCustomer | CommercialCustomer) {
    this.selectedCustomer.set(this.enrichCustomer(customer));
    this.customerTab = 'overview';
  }

  closeCustomer() {
    this.selectedCustomer.set(null);
    this.grantDraft = null;
  }

  selectCustomerTab(tab: string) {
    this.customerTab = tab;
  }

  openCustomerSubscription(customerId: string) {
    const customer = this.customerRows.find((value) => value.id === customerId);
    if (customer) this.openCustomer(customer);
    this.customerTab = 'subscription';
  }

  requestDisableCustomer(customer: DashboardCustomer) {
    const enabling = !this.isEnabled(customer.enabled);
    this.openConfirmation({
      title: enabling ? `恢复客户服务：${customer.display_name}` : `停用客户服务：${customer.display_name}`,
      description: enabling ? '恢复后，客户设备仍需重新建立有效租约。' : '这会停止客户认证并撤销该客户当前仍有效的租约；设备身份和审计记录会保留。',
      consequence: enabling ? '恢复不会生成新的设备或租约。' : '现有设备请求会被拒绝，当前租约会失效；这不是删除客户数据。',
      payload: { action: 'customer-enable', id: customer.id, enabled: enabling },
      success: enabling ? '客户服务已恢复' : '客户服务已停用，活动租约已撤销',
    });
  }

  requestRevokeDevice(device: CommercialDevice) {
    const enabling = !this.isEnabled(device.enabled);
    this.openConfirmation({
      title: enabling ? `恢复设备：${device.label || device.id}` : `撤销设备：${device.label || device.id}`,
      description: enabling ? '恢复后设备可以重新请求租约，但不会改变其公钥或客户归属。' : '撤销会拒绝该设备后续认证，并撤销它的活动租约；设备记录不会被删除。',
      consequence: enabling ? '请确认这是仍受控的设备。' : '不得把撤销当作凭据轮换；当前 API 没有提供凭据轮换能力。',
      payload: { action: 'device-enable', id: device.id, enabled: enabling },
      success: enabling ? '设备已恢复' : '设备已撤销，活动租约已失效',
    });
  }

  openReissue(customer: DashboardCustomer | CommercialCustomer) {
    this.openConfirmation({
      title: `为 ${customer.display_name} 补发一次性注册链接`,
      description: '服务端会生成新的短期开户地址，不会修改客户的现有套餐或设备。',
      consequence: '链接只在本次确认后展示；请通过受控渠道交付，不要公开分享。页面不会保存链接历史。',
      payload: { action: 'subscription-reissue', id: customer.id, base_url: this.baseUrl },
      success: '一次性注册链接已生成',
      after: (value) => this.oneTimeResult.set(value),
    });
  }

  startSubscriptionWizard(mode: 'new' | 'reissue' = 'new') {
    this.subscriptionMode = mode;
    this.wizardStep = 1;
    this.oneTimeResult.set(null);
    this.message.set('');
    if (mode === 'reissue' && !this.reissueCustomerId) this.reissueCustomerId = this.customerRows[0]?.id || '';
  }

  nextWizardStep() {
    if (this.wizardStep === 1 && this.subscriptionMode === 'new' && !this.customerName.trim()) {
      this.message.set('请先填写新客户名称');
      return;
    }
    if (this.wizardStep === 1 && this.subscriptionMode === 'reissue' && !this.reissueCustomerId) {
      this.message.set('请选择要补发链接的客户');
      return;
    }
    if (this.wizardStep === 3 && !this.selectedSourceIds.length) {
      this.message.set('至少选择一个允许节点');
      return;
    }
    this.message.set('');
    this.wizardStep = Math.min(4, this.wizardStep + 1);
  }

  previousWizardStep() {
    this.wizardStep = Math.max(1, this.wizardStep - 1);
  }

  chooseQuota(value: number | null) {
    this.quotaGb = value;
  }

  toggleSource(sourceId: string, checked: boolean) {
    this.selectedSourceIds = checked
      ? Array.from(new Set([...this.selectedSourceIds, sourceId]))
      : this.selectedSourceIds.filter((id) => id !== sourceId);
    this.proxySourceIds = this.proxySourceIds.filter((id) => this.selectedSourceIds.includes(id));
  }

  toggleProxySource(sourceId: string, checked: boolean) {
    this.proxySourceIds = checked
      ? Array.from(new Set([...this.proxySourceIds, sourceId]))
      : this.proxySourceIds.filter((id) => id !== sourceId);
  }

  proxySourceAllowed(source: CommercialSource) {
    return this.selectedSourceIds.includes(source.id) && (this.snapshot().local_proxy_source_ids || []).includes(source.id);
  }

  generateSubscription() {
    if (this.subscriptionMode === 'reissue') {
      const customer = this.customerRows.find((value) => value.id === this.reissueCustomerId);
      if (customer) this.openReissue(customer);
      return;
    }
    this.openConfirmation({
      title: `签发订阅：${this.customerName.trim()}`,
      description: '服务端会创建客户、套餐记录、线路授权和一次性开户令牌。',
      consequence: '开户链接有效期为 24 小时且只能使用一次；订阅期限由服务端按当前能力签发为 30 天。请不要公开分享链接。',
      payload: {
        action: 'subscription-generate',
        name: this.customerName.trim(),
        base_url: this.baseUrl,
        source_ids: this.selectedSourceIds,
        proxy_source_ids: this.proxySourceIds,
        quota_gb: this.quotaGb,
        download_bps: this.rateToBps(this.downloadMbps),
        upload_bps: this.rateToBps(this.uploadMbps),
      },
      success: '订阅已签发；链接仅在当前页面展示',
      after: (value) => this.oneTimeResult.set(value),
    });
  }

  hideOneTimeResult() {
    this.oneTimeResult.set(null);
  }

  copyOneTimeUrl() {
    const url = this.oneTimeResult()?.url;
    if (!url) return;
    navigator.clipboard.writeText(url).then(
      () => this.message.set('链接已复制；请立即通过受控渠道交付'),
      () => this.message.set('浏览器不允许剪贴板操作，请手动选择复制；不要把链接贴到公开频道'),
    );
  }

  startNewSource() {
    this.sourceDraft = {
      name: '',
      endpoint: '',
      relay_public_key: '',
      address_pool: '',
      relay_interface: '',
      egress_interface: '',
      egress_mode: 'physical',
      egress_gateway: '',
      proxy_interface: 'Meta',
      dns: '223.5.5.5,1.1.1.1',
    };
  }

  editSource(source: CommercialSource) {
    this.sourceDraft = { ...source };
  }

  closeSourceEditor() {
    this.sourceDraft = null;
  }

  setSourceField(field: string, value: unknown) {
    if (this.sourceDraft) this.sourceDraft[field] = value;
  }

  saveSource() {
    if (!this.sourceDraft) return;
    this.openConfirmation({
      title: this.sourceDraft.id ? `保存节点：${this.sourceDraft.name}` : '登记新节点',
      description: '保存会更新以后签发的线路配置；已有租约不会被这个保存动作悄悄改写。',
      consequence: '请确认中继接口、出口接口和网关属于受控服务器；错误配置可能使新租约不可用。',
      payload: { action: 'source-save', source: this.sourceDraft, expected_revision: this.sourceDraft.revision },
      success: '节点配置已保存',
      after: () => this.closeSourceEditor(),
    });
  }

  toggleSourceEnabled(source: CommercialSource) {
    const enabled = !this.sourceEnabled(source);
    this.openConfirmation({
      title: `${enabled ? '启用' : '停用'}节点：${source.name}`,
      description: enabled ? '启用后该节点可以继续被新的授权使用。' : '停用会阻止新的授权使用；已有启用线路需要先迁移或停用，系统不会静默删除历史。',
      consequence: '节点状态改变会影响后续租约建立，请确认变更窗口。',
      payload: { action: 'source-enable', id: source.id, enabled, expected_revision: source.revision },
      success: enabled ? '节点已启用' : '节点已停用',
    });
  }

  deleteSource(source: CommercialSource) {
    this.openConfirmation({
      title: `移除节点：${source.name}`,
      description: '后端会在仍有历史引用时保留归档记录；存在启用客户线路时会拒绝移除。',
      consequence: '这是节点管理动作，不会删除客户、设备或审计记录；请先确认授权关系。',
      payload: { action: 'source-delete', id: source.id, expected_revision: source.revision },
      success: '节点移除请求已完成',
    });
  }

  startSourceProxy() {
    this.openConfirmation({
      title: '启动源机代理服务',
      description: '启动当前服务端已配置的 Mihomo/TUN 进程。',
      consequence: '这不会替所有客户切换出口，也不会改写现有订阅；只有明确选择代理出口的线路会使用它。',
      payload: { action: 'source-proxy-start' },
      success: '源机代理启动请求已完成',
    });
  }

  grantForSource(source: CommercialSource) {
    return this.snapshot().grants.filter((grant) => {
      const policy = this.policy(grant);
      return policy.source_id === source.id || (!policy.source_id && grant.endpoint === source.endpoint && grant.relay_interface === source.relay_interface);
    });
  }

  grantExpiry(source: CommercialSource) {
    const values = this.grantForSource(source).map((grant) => grant.expires_at).filter((value): value is number => typeof value === 'number');
    return values.length ? Math.min(...values) : null;
  }

  editGrant(grant: CommercialGrant) {
    this.grantDraft = { ...grant, ...this.policy(grant) };
  }

  cancelGrantEdit() {
    this.grantDraft = null;
  }

  saveGrant() {
    if (!this.grantDraft) return;
    this.openConfirmation({
      title: `更新授权出口：${this.grantDraft.alias}`,
      description: '出口策略更新会撤销该线路的旧租约，客户需要退网后重新入网以获得新 DNS/出口参数。',
      consequence: '客户会短暂失去该线路的当前租约；此动作不改变源机代理全局开关。',
      payload: {
        action: 'grant-egress',
        id: this.grantDraft.id,
        egress_mode: this.grantDraft.egress_mode,
        egress_interface: this.grantDraft.egress_interface,
        egress_gateway: this.grantDraft.egress_mode === 'physical' ? this.grantDraft.egress_gateway : '',
        dns: this.grantDraft.dns,
      },
      success: '授权出口已更新，旧租约已撤销',
      after: () => this.cancelGrantEdit(),
    });
  }

  requestLeaseRevoke(lease: CommercialLease) {
    this.openConfirmation({
      title: `撤销租约：${lease.id}`,
      description: '撤销只影响这条短期租约，不会删除客户、设备或线路授权。',
      consequence: '客户端需要重新申请租约；中继端会在下一次 reconciliation 后移除该 peer。',
      payload: { action: 'lease-revoke', id: lease.id },
      success: '租约已撤销',
    });
  }

  leaseStatusOf(lease: CommercialLease) {
    if (lease.revoked_at !== null) return 'revoked';
    return lease.expires_at <= this.now() ? 'expired' : 'active';
  }

  leaseStatusLabel(lease: CommercialLease) {
    const status = this.leaseStatusOf(lease);
    return status === 'active' ? 'Active' : status === 'expired' ? 'Expired' : 'Revoked';
  }

  leaseStatusClass(lease: CommercialLease) {
    return `status-${this.leaseStatusOf(lease)}`;
  }

  leaseCustomer(lease: CommercialLease) {
    return this.customerRows.find((customer) => customer.id === lease.customer_id)?.display_name || lease.customer_id;
  }

  customerNameById(customerId: string) {
    return this.customerRows.find((customer) => customer.id === customerId)?.display_name || customerId;
  }

  customerPlanForLease(lease: CommercialLease) {
    return this.customerPlan(this.customerRows.find((customer) => customer.id === lease.customer_id) || { plan_id: '' });
  }

  leaseDevice(lease: CommercialLease) {
    const device = this.snapshot().devices.find((value) => value.id === lease.device_id);
    return device?.label || lease.device_id;
  }

  leaseGrant(lease: CommercialLease) {
    return this.snapshot().grants.find((grant) => grant.id === lease.grant_id);
  }

  leaseEgress(lease: CommercialLease) {
    const grant = this.leaseGrant(lease);
    return grant ? this.egressLabel(grant) : '—';
  }

  openConfirmation(request: ConfirmationRequest) {
    this.reauthPassword = '';
    this.pendingAction.set(request);
  }

  cancelConfirmation() {
    this.reauthPassword = '';
    this.pendingAction.set(null);
  }

  confirmPendingAction() {
    const request = this.pendingAction();
    if (!request) return;
    if (!this.reauthPassword.trim()) {
      this.error.set('请输入管理员密码完成 fresh() 重新验证');
      return;
    }
    this.error.set('');
    this.busy.set(true);
    this.api.post('/api/reauth', { password: this.reauthPassword }).subscribe({
      next: () => this.api.post<any>('/api/client-service/action', request.payload).subscribe({
        next: (value) => {
          request.after?.(value);
          this.oneTimeResult.set(request.payload['action'] === 'subscription-generate' || request.payload['action'] === 'subscription-reissue' ? value : this.oneTimeResult());
          this.message.set(request.success);
          this.busy.set(false);
          this.cancelConfirmation();
          queueMicrotask(() => this.reload(true));
        },
        error: (error) => {
          this.busy.set(false);
          this.error.set(error.message);
        },
      }),
      error: (error) => {
        this.busy.set(false);
        this.error.set(`重新验证失败：${error.message}`);
      },
    });
  }

  isEnabled(value: unknown) {
    return value === true || value === 1;
  }

  sourceEnabled(source: CommercialSource) {
    return source.enabled !== false && !source.archived;
  }

  sourceStatusLabel(source: CommercialSource) {
    if (source.archived) return '已归档';
    return this.sourceEnabled(source) ? '启用' : '停用';
  }

  sourceStatusClass(source: CommercialSource) {
    return this.sourceEnabled(source) ? 'status-active' : source.archived ? 'status-archived' : 'status-disabled';
  }

  egressLabel(grant: CommercialGrant) {
    const mode = this.policy(grant).egress_mode || 'source_proxy';
    return mode === 'physical' ? `物理出口 · ${grant.egress_interface}` : `源机代理 · ${grant.egress_interface}`;
  }

  policy(grant: CommercialGrant | any) {
    try {
      return JSON.parse(grant.egress_policy || '{}');
    } catch {
      return {};
    }
  }

  formatDate(value: number | null | undefined) {
    return value ? new Date(value * 1000).toLocaleString() : '—';
  }

  formatBytes(value: number | null | undefined) {
    if (value === null || value === undefined) return '无限';
    if (!value) return '0 B';
    const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    let amount = value;
    let index = 0;
    while (amount >= 1024 && index < units.length - 1) {
      amount /= 1024;
      index += 1;
    }
    return `${amount.toFixed(amount >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
  }

  formatRate(value: number | null | undefined) {
    if (value === null || value === undefined) return '不限速';
    return `${(value / 1_000_000).toFixed(value % 1_000_000 ? 1 : 0)} Mbps`;
  }

  planQuota(plan: CommercialPlan | undefined) {
    return plan ? this.formatBytes(plan.quota_bytes) : '—';
  }

  rateToBps(value: number) {
    return Math.max(0, Math.round(Number(value || 0) * 1_000_000));
  }

  nodePriority() {
    return '后端未提供';
  }

  endpointAccess() {
    return '按客户 Grant 授权';
  }

  fingerprint(device: CommercialDevice) {
    return this.fingerprints()[device.id] || '计算中…';
  }

  auditObjectType(event: AuditEvent) {
    const value = `${event.action} ${event.target}`.toLowerCase();
    if (value.includes('customer') || value.includes('subscription')) return 'Customer / Subscription';
    if (value.includes('device')) return 'Device';
    if (value.includes('lease')) return 'Lease';
    if (value.includes('grant') || value.includes('source')) return 'Grant / Node';
    return 'System';
  }

  auditRevision(event: AuditEvent) {
    const revision = event.details?.['revision'];
    return typeof revision === 'string' ? revision : '未记录';
  }

  auditSummary(event: AuditEvent) {
    return this.safeJson(event.details) || event.action;
  }

  customerAuditEvents(customerId: string) {
    return this.events().filter((event) => event.target === customerId || event.details?.['customer_id'] === customerId);
  }

  safeJson(value: unknown) {
    const redacted = this.redact(value);
    try {
      return JSON.stringify(redacted);
    } catch {
      return '详情不可读取';
    }
  }

  private redact(value: unknown): unknown {
    const sensitive = /token|authorization|private.?key|wireguard.?private|password|secret|bearer/i;
    if (Array.isArray(value)) return value.map((item) => this.redact(item));
    if (value && typeof value === 'object') {
      return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, sensitive.test(key) ? '[REDACTED]' : this.redact(item)]));
    }
    return value;
  }

  private enrichCustomer(customer: CommercialCustomer | DashboardCustomer): DashboardCustomer {
    const plan = this.customerPlan(customer) || {
      id: customer.plan_id,
      name: '未读取套餐',
      download_bps: null,
      upload_bps: null,
      quota_bytes: null,
      period_seconds: 0,
      max_devices: 0,
      lease_seconds: 0,
      enabled: false,
      created_at: 0,
    };
    const devices = this.customerDevices(customer);
    const leases = this.customerLeases(customer);
    const grants = this.customerGrants(customer).map((grant) => ({
      id: grant.id,
      name: grant.alias,
      source_id: this.policy(grant).source_id || null,
      source_name: grant.alias,
      endpoint: grant.endpoint,
      enabled: this.isEnabled(grant.enabled),
      expires_at: grant.expires_at,
      egress_mode: this.policy(grant).egress_mode || 'source_proxy',
      available: this.isEnabled(grant.enabled),
      reasons: [],
    }));
    const existing = customer as DashboardCustomer;
    return {
      ...customer,
      plan,
      usage: existing.usage || { used_bytes: 0, remaining_bytes: plan.quota_bytes, quota_bytes: plan.quota_bytes },
      grants: existing.grants || grants,
      devices: existing.devices || devices,
      leases: existing.leases || leases,
      active_lease_count: existing.active_lease_count ?? leases.filter((lease) => this.leaseStatusOf(lease) === 'active').length,
      usable: existing.usable ?? this.isEnabled(customer.enabled),
      reasons: existing.reasons || [],
      version: existing.version || 0,
      revision: existing.revision || '',
    };
  }

  private async loadFingerprints(devices: CommercialDevice[]) {
    if (!globalThis.crypto?.subtle) return;
    const values = await Promise.all(devices.map(async (device) => [device.id, await this.keyFingerprint(device.public_key)] as const));
    this.fingerprints.set(Object.fromEntries(values));
  }

  private async keyFingerprint(value: string) {
    try {
      const normalized = value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (value.length % 4)) % 4);
      const bytes = Uint8Array.from(atob(normalized), (char) => char.charCodeAt(0));
      const digest = new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256', bytes));
      return `SHA256:${Array.from(digest).map((byte) => byte.toString(16).padStart(2, '0')).join('').slice(0, 24)}`;
    } catch {
      return '不可计算';
    }
  }
}
