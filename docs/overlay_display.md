# Display settings / 表示設定

Generated from `core/overlay_policy.py`. Do not edit this table by hand.

All values are shared by views of the same scene. / 設定値は同じシーンの各画面で共有します。

| ID | Group / 区画 | Label / 名前 | Destination / 表示先 | Dependency / 依存 | Entrances / 入口 |
|---|---|---|---|---|---|
| cloth.show_overlays | — | Overlays / オーバーレイ | Pattern & 3D | — | ui.draw_overlay_switch, ui.AC9_PT_SimOverlays |
| cloth.show_overlay | 縫い目 | Seams / 縫い目 | 3D | — | ui.AC9_PT_SimOverlays |
| cloth.show_pattern_seams_always | 縫い目 | Show Seams with Other Tools / 他のツールでも縫い目を表示 | Pattern | — | ui.AC9_PT_SimOverlays |
| cloth.show_lengths | 縫い目 | Selected Seam Lengths / 選択した縫い目の長さ | Pattern | — | ui.AC9_PT_SimOverlays |
| cloth.show_notches | 縫い目 | Notches / 合印 | Pattern | — | ui.AC9_PT_SimOverlays |
| cloth.show_piece_colors | ピース | Piece Colors / ピースの色 | Pattern & 3D | — | ui.AC9_PT_SimOverlays |
| cloth.show_fabric_colors | ピース | Color by Fabric / 生地で色分け | Pattern & 3D | show_piece_colors | ui.AC9_PT_SimOverlays, ui.draw_fabric_list |
| cloth.show_names | ピース | Piece Names / ピースの名前 | Pattern | — | ui.AC9_PT_SimOverlays |
| cloth.show_internal_3d | ピース | Internal Lines / 内部線 | 3D | — | ui.AC9_PT_SimOverlays |
| measure.show_body | 採寸 | Body & Garment Measurements / 身体・衣服の採寸線 | 3D | — | ui.AC9_PT_SimOverlays, measure_tool.AC9_PT_SimMeasure |
| measure.show | 採寸 | Measuring Tapes / 採寸テープ | Pattern & 3D | — | ui.AC9_PT_SimOverlays, measure_tool.AC9_PT_SimMeasure |
| cloth.show_guides | 配置・シルエット | Placement Guides / 配置ガイド | 3D | — | ui.AC9_PT_SimOverlays, ui.AC9_PT_SimPlacement |
| cloth.show_silhouette_avatar | 配置・シルエット | Avatar Silhouette / アバターのシルエット | Pattern | — | ui.AC9_PT_SimOverlays |
| cloth.show_silhouette_collisions | 配置・シルエット | Collision Silhouettes / コリジョンのシルエット | Pattern | — | ui.AC9_PT_SimOverlays |
| cloth.silhouette_position | 配置・シルエット | Move Silhouettes / シルエットの位置を変更 | Pattern | — | ui.AC9_PT_SimOverlays |
| cloth.diagnosis_display | 診断の表示 | Diagnosis Display / 診断の表示 | PROBLEMS: Pattern & 3D; STRETCH: 3D; SIDE: 3D | — | ui.AC9_PT_SimOverlays, ui.AC9_PT_SimDiagnosis |
| cloth.show_thickness | 服 | Show Fabric Thickness / 生地の厚みを表示 | 3D | — | ui.AC9_PT_SimOverlays |
| cloth.thickness_side | 服 | Direction / 方向 | 3D | show_thickness | ui.AC9_PT_SimOverlays |

Garment thickness is outside the overlay master switch; selection highlighting may remain visible.
厚みは補助表示の主スイッチ外。選択中の強調表示は残る場合があります。
