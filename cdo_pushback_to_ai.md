# Phản hồi từ CDO về AI API Contract (Tích hợp Slack & Jira)

Chào team AI, 

Team CDO đã review xong bản draft `ai-api-contract.md` (đặc biệt là mục 5 về Slack/Jira setup). Về cơ bản, các output fields (confidence, severity, suspected_root_cause...) đã khá đầy đủ. 

Tuy nhiên, để tối ưu hóa kiến trúc, giảm tải cho AI và tăng trải nghiệm người dùng (đúng chuẩn SRE), CDO đề xuất 2 điểm thay đổi (Push-back) quan trọng sau đây để chốt lại trong buổi Co-design sáng Thứ 5:

## 1. Tách biệt UI và Logic trên Slack (Decoupling Presentation)

**Vấn đề hiện tại:** Team AI đang dự định tự render ra `slack_payload` dạng plain text để báo cáo sự cố.
**Đề xuất của CDO:** 
* Bên team CDO đã thiết kế sẵn một template Slack UI bằng **Block Kit** (có chia block, highlight rõ ràng và có nút Interactive "Acknowledge"). Do đó, block text mà AI trả về sẽ không tương thích.
* **Chốt phương án (Integration transforms):** Team AI **KHÔNG CẦN** phải render ra `slack_payload` định dạng sẵn nữa. 
* Các bạn chỉ cần trả về cục raw data JSON chứa `suspected_root_cause`, `recommended_actions`, `confidence`, `incident_id`... 
* Bên Lambda của CDO sẽ tự động lấy các data này và inject (bơm) vào file Block Kit Template của bên mình rồi bắn lên Slack. Như vậy AI sẽ nhẹ việc hơn (chỉ tập trung vào logic lõi) và UI trên Slack cũng chuyên nghiệp hơn rất nhiều.

## 2. Nâng cấp luồng gán Jira: Thay vì "Unassigned", chuyển sang "AI Suggestion + Human-in-the-loop"

**Vấn đề hiện tại:** Trong contract (mục 5), team AI ghi là chưa đủ data để auto-assign cá nhân nên sẽ tạo ticket dạng "Unassigned" và để team tự chia nhau trên Slack.

**Đề xuất của CDO:** 
Thay vì để Unassigned hoàn toàn hoặc dùng công cụ chia ca phức tạp như PagerDuty (hơi over-engineer cho Demo), chúng ta sẽ làm luồng **"Máy đề xuất - Người duyệt" (Human-in-the-loop)**.

* **Nhiệm vụ của AI:** AI sẽ lấy thêm context từ lịch sử Jira. Dựa vào dữ liệu các ticket cũ (ai hay fix lỗi service này, ai là component lead...), AI sẽ phân tích và đưa ra **gợi ý (suggestion)** người xử lý phù hợp nhất.
* **Cập nhật Schema API:** Đề nghị team AI bổ sung thêm 2 trường sau vào Payload JSON trả về cho CDO:
  1. `suggested_assignee_account_id`: (String) Jira Account ID của người được gợi ý.
  2. `suggestion_reason`: (String) Lý do gợi ý ngắn gọn (Vd: *"Dựa trên 5 ticket gần nhất, user này là SME của checkout-api"*).
* **Nhiệm vụ của CDO:** CDO sẽ lấy dữ liệu gợi ý này hiển thị lên giao diện Slack Block Kit, kèm theo một nút bấm **[ ✅ Confirm & Assign to @User ]**. Khi Tech Lead hoặc nhân sự trực ca đọc tin nhắn, thấy gợi ý hợp lý và bấm nút này, hệ thống CDO (thông qua Webhook và Lambda) sẽ tự động gọi API lên Jira để gán (assign) đích danh người đó.

---

## 3. Ví dụ JSON Payload Mong muốn (Expected Response Format)

Để làm rõ 2 yêu cầu trên, team CDO đề nghị API `POST /v1/triage` trả về JSON với cấu trúc thô (raw data), bổ sung trường gợi ý assignee và bỏ hẳn cục `slack_payload` chứa text dựng sẵn. Dưới đây là cấu trúc mong đợi mà CDO cần để inject vào Slack Block Kit:

```json
{
  "incident_id": "inc-001",
  "classification": "latency_degradation",
  "severity": "high",
  "confidence": 0.82,
  "status": "DIAGNOSED",
  "suspected_root_cause": {
    "summary": "Recent checkout-api deploy likely introduced a slower DB query path.",
    "evidence": [
      "p95 latency increased from 220ms to 950ms after sha-a1b2c3",
      "error logs show database timeout after 3000ms"
    ]
  },
  "recommended_actions": [
    {
      "type": "HUMAN_REVIEW",
      "priority": 1,
      "summary": "Check DB connection saturation and slow query logs.",
      "runbook_ref": "runbook://db-timeout"
    }
  ],
  "ticket_payload": {
    "project": "PAY",
    "summary": "[high] checkout-api latency degradation",
    "description": "AI triage summary with evidence and next steps.",
    "labels": ["ai-triage", "tenant-a", "checkout-api"],
    "fields": {
      "confidence": 0.82,
      "owner_team": "payments-platform",
      "audit_id": "audit-001"
    }
  },
  "suggested_assignee_account_id": "U123456",
  "suggestion_reason": "Dựa trên 5 ticket gần nhất, user này là SME của checkout-api module.",
  "audit_id": "audit-001"
}
```
*(Lưu ý: Không còn trường `slack_payload` chứa text dựng sẵn trong JSON này. Hệ thống Lambda của CDO sẽ tự động bóc tách các trường như `suspected_root_cause`, `confidence`, `suggested_assignee_account_id`... để fill (inject) vào template Slack Block Kit).*

---

**Tổng kết:** 
Với 2 thay đổi này, kiến trúc hệ thống của Task Force chúng ta sẽ rất rành mạch: **AI chuyên tâm vào phân tích (Log/Metric/Jira History), còn CDO chuyên tâm vào hạ tầng, kết nối và giao diện (Slack Block Kit/Jira API).** Sự kết hợp này chắc chắn sẽ là một điểm nhấn cực mạnh (Differentiation Angle) cho nhóm chúng ta trong buổi chấm thi.

Team AI confirm lại khả năng lấy `account_id` từ Jira history để bên CDO chốt schema mới và cập nhật lại thẳng vào `ai-api-contract.md` nhé!
