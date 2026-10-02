import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { CommercialComponent } from './commercial.component';
import { ApiService } from './api.service';
import { CommercialSnapshot, DashboardSnapshot } from './models';

const now = Math.floor(Date.now() / 1000);
const snapshot: CommercialSnapshot = {
  sources: [{
    id: 'node-1', name: '源网一', endpoint: '198.51.100.10:51820', relay_public_key: 'relay-public',
    address_pool: '10.203.0.0/24', relay_interface: 'wg-source', egress_interface: 'enp4s0',
    egress_mode: 'physical', enabled: true, archived: false, revision: 'source-rev-1',
  }],
  plans: [{ id: 'plan-1', name: '50 GB', download_bps: 50_000_000, upload_bps: 10_000_000, quota_bytes: 50 * 1024 ** 3, period_seconds: 30 * 86400, max_devices: 2, lease_seconds: 900, enabled: true, created_at: now }],
  customers: [{ id: 'cus-1', display_name: '测试客户', plan_id: 'plan-1', enabled: true, revoked_at: null, created_at: now - 100 }],
  subscription_addresses: [{ customer_id: 'cus-1', expires_at: now + 3600, used_at: null, created_at: now - 100, available: true, can_reissue: true, status: 'ready' }],
  devices: [{ id: 'dev-1', customer_id: 'cus-1', label: '办公室笔记本', public_key: 'cHVibGljLWtleQ', wireguard_public_key: 'd2lyZWd1YXJk', enabled: true, revoked_at: null, created_at: now - 50, last_seen_at: now - 5 }],
  grants: [{ id: 'grant-1', customer_id: 'cus-1', alias: '源网一', tunnel: 'customer-online', endpoint: '198.51.100.10:51820', enabled: true, relay_public_key: 'relay-public', allocated_address: '10.203.0.2/32', dns: '1.1.1.1', allowed_ips: '0.0.0.0/1,128.0.0.0/1', mtu: 1420, relay_interface: 'wg-source', egress_interface: 'enp4s0', egress_policy: '{"egress_mode":"physical","source_id":"node-1"}', expires_at: now + 86400, created_at: now - 100 }],
  leases: [{ id: 'lease-1', customer_id: 'cus-1', device_id: 'dev-1', grant_id: 'grant-1', issued_at: now - 10, expires_at: now + 890, revoked_at: null, last_rx: 1024, last_tx: 2048 }],
};
const dashboard: DashboardSnapshot = {
  generated_at: now,
  customers: [{ ...snapshot.customers[0], plan: snapshot.plans[0], usage: { used_bytes: 3072, remaining_bytes: snapshot.plans[0].quota_bytes, quota_bytes: snapshot.plans[0].quota_bytes }, grants: [{ id: 'grant-1', name: '源网一', source_id: 'node-1', source_name: '源网一', endpoint: '198.51.100.10:51820', enabled: true, expires_at: now + 86400, egress_mode: 'physical', available: true, reasons: [] }], devices: snapshot.devices, leases: snapshot.leases, active_lease_count: 1, usable: true, reasons: [], version: 1, revision: 'customer-rev-1' }],
  summary: { customers: 1, usable: 1, active_leases: 1, today_bytes: 0, total_bytes: 3072 },
};

describe('CommercialComponent', () => {
  let fixture: ComponentFixture<CommercialComponent>;
  let component: CommercialComponent;
  let api: { get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn>; invalidateNavigation: ReturnType<typeof vi.fn> };

  beforeEach(async () => {
    api = {
      get: vi.fn((path: string) => path === '/api/client-service' ? of(snapshot) : path === '/api/client-service/dashboard' ? of(dashboard) : of({ events: [] })),
      post: vi.fn(() => of({ ok: true })),
      invalidateNavigation: vi.fn(),
    };
    await TestBed.configureTestingModule({
      imports: [CommercialComponent],
      providers: [{ provide: ApiService, useValue: api }],
    }).compileComponents();
    fixture = TestBed.createComponent(CommercialComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('active', true);
    fixture.componentRef.setInput('section', 'customers');
    fixture.detectChanges();
    await fixture.whenStable();
  });

  it('renders customer table fields from the real snapshot', () => {
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('测试客户');
    expect(text).toContain('cus-1');
    expect(text).toContain('50 GB');
    expect(text).toContain('启用');
  });

  it('requires a visible confirmation and reauth before disabling a customer', () => {
    component.requestDisableCustomer(component.customerRows[0]);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('停用客户服务：测试客户');
    expect(api.post).not.toHaveBeenCalledWith('/api/client-service/action', expect.anything());

    component.reauthPassword = 'not-a-real-test-password';
    component.confirmPendingAction();
    expect(api.post).toHaveBeenCalledWith('/api/reauth', { password: 'not-a-real-test-password' });
    expect(api.post).toHaveBeenCalledWith('/api/client-service/action', { action: 'customer-enable', id: 'cus-1', enabled: false });
  });

  it('shows the honest unsupported directory state instead of fake revision data', () => {
    fixture.componentRef.setInput('section', 'directory');
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('当前 API 未接入');
    expect(fixture.nativeElement.textContent).toContain('不提供伪造的“预览下一版 / 发布”按钮');
    expect(fixture.nativeElement.textContent).toContain('signing public key fingerprint');
  });

  it('keeps disabled customer state visible and filterable', () => {
    const disabled = { ...snapshot.customers[0], enabled: false, revoked_at: now };
    component.snapshot.set({ ...snapshot, customers: [disabled] });
    component.dashboard.set(null);
    component.customerStatus = 'disabled';
    fixture.detectChanges();
    expect(component.filteredCustomers).toHaveLength(1);
    expect(fixture.nativeElement.textContent).toContain('服务停用');
  });
});
