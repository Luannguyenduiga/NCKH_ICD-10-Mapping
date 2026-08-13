package com.vnpt.his.backend.model;

import jakarta.persistence.*;

@Entity
@Table(name = "drug_items")
public class DrugItem {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String code; // D001, D002...
    private String name;
    private String unit; // Viên, Hộp, Vỉ, Chai
    private Double price;
    private Integer stock;
    private String usageGuide;

    public DrugItem() {}

    public DrugItem(String code, String name, String unit, Double price, Integer stock, String usageGuide) {
        this.code = code;
        this.name = name;
        this.unit = unit;
        this.price = price;
        this.stock = stock;
        this.usageGuide = usageGuide;
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public String getCode() { return code; }
    public void setCode(String code) { this.code = code; }

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }

    public String getUnit() { return unit; }
    public void setUnit(String unit) { this.unit = unit; }

    public Double getPrice() { return price; }
    public void setPrice(Double price) { this.price = price; }

    public Integer getStock() { return stock; }
    public void setStock(Integer stock) { this.stock = stock; }

    public String getUsageGuide() { return usageGuide; }
    public void setUsageGuide(String usageGuide) { this.usageGuide = usageGuide; }
}
